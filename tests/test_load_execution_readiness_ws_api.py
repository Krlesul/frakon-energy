from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_readiness_ws_api as readiness_ws
from custom_components.frakon_energy.const import DOMAIN
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import (
    ExecutionActionSnapshot,
    ExecutionActionSnapshotRepository,
)
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import (
    ExecutionAttempt,
    ExecutionAttemptRepository,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
    upsert_execution_policy,
)
from custom_components.frakon_energy.load_execution_readiness import (
    READINESS_BLOCKED,
    READINESS_READY,
    REASON_SCOPE_CHANGED,
    ExecutionReadinessError,
)
from custom_components.frakon_energy.load_profiles import (
    PROFILE_KIND_EV,
    LoadProfile,
    upsert_profile,
)

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)


class _FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = data


class _FakeEntry:
    domain = DOMAIN
    entry_id = "entry-1"

    def __init__(self, options: dict[str, Any]) -> None:
        self.options = options


class _FakeConfigEntries:
    def __init__(self, entry: _FakeEntry) -> None:
        self.entry = entry

    def async_get_entry(self, entry_id: str) -> _FakeEntry | None:
        return self.entry if entry_id == self.entry.entry_id else None


class _FakeStates:
    def __init__(self, states: dict[str, str]) -> None:
        self._states = states

    def get(self, entity_id: str) -> object | None:
        value = self._states.get(entity_id)
        return SimpleNamespace(state=value) if value is not None else None


class _FakeHass:
    def __init__(self, options: dict[str, Any], state: str = "off") -> None:
        self.data: dict[str, Any] = {}
        self.config_entries = _FakeConfigEntries(_FakeEntry(options))
        self.states = _FakeStates({"switch.enyaq_charging": state})


def _profile(*, entity_id: str = "switch.enyaq_charging") -> LoadProfile:
    return LoadProfile(
        "ev-home",
        "Enyaq",
        PROFILE_KIND_EV,
        120,
        11.0,
        entity_id=entity_id,
    )


def _policy() -> LoadExecutionPolicy:
    return LoadExecutionPolicy(
        "ev-home",
        mode=EXECUTION_MODE_APPROVAL_REQUIRED,
        max_power_kw=11.0,
        max_duration_minutes=120,
    )


def _plan() -> LoadPlan:
    duration = 120
    power = 11.0
    average = 2.0
    energy = power * duration / 60
    return LoadPlan(
        load_id="ev-home",
        name="Enyaq",
        starts_at=START.isoformat(),
        ends_at=(START + timedelta(minutes=duration)).isoformat(),
        duration_minutes=duration,
        interval_count=8,
        power_kw=power,
        average_czk_kwh=average,
        minimum_czk_kwh=1.5,
        maximum_czk_kwh=2.5,
        estimated_energy_kwh=energy,
        estimated_cost_czk=energy * average,
    )


def _options(profile: LoadProfile | None = None) -> dict[str, Any]:
    options: dict[str, Any] = upsert_profile({}, profile or _profile())
    return upsert_execution_policy(options, _policy())


def _attempt() -> ExecutionAttempt:
    return ExecutionAttempt(
        attempt_id="attempt-1",
        entry_id="entry-1",
        profile_id="ev-home",
        entity_id="switch.enyaq_charging",
        approval_id="approval-1",
        approval_fingerprint="a" * 64,
        snapshot_digest=execution_snapshot_digest(_profile(), _plan(), _policy()),
        intent="execute_load_plan",
        approval_issued_at=int((START - timedelta(minutes=5)).timestamp()),
        approval_expires_at=int((START + timedelta(minutes=5)).timestamp()),
        created_at=int((START - timedelta(minutes=1)).timestamp()),
    ).validated()


def _snapshot(attempt: ExecutionAttempt) -> ExecutionActionSnapshot:
    return ExecutionActionSnapshot.from_attempt_and_intent(
        attempt=attempt,
        intent=resolve_start_action_intent(_profile()),
        created_at=attempt.created_at,
    )


async def _install_repositories(monkeypatch: pytest.MonkeyPatch):
    attempt_repository = ExecutionAttemptRepository(_FakeStore())
    snapshot_repository = ExecutionActionSnapshotRepository(_FakeStore())
    attempt = _attempt()
    snapshot = _snapshot(attempt)
    await attempt_repository.async_record(attempt)
    await snapshot_repository.async_record(snapshot)
    monkeypatch.setattr(
        readiness_ws.consume_ws,
        "_attempt_repository",
        lambda hass, entry_id: attempt_repository,
    )
    monkeypatch.setattr(
        readiness_ws,
        "action_snapshot_repository",
        lambda hass, entry_id: snapshot_repository,
    )
    return attempt_repository, snapshot_repository, attempt, snapshot


@pytest.mark.asyncio
async def test_readiness_endpoint_returns_ready_without_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass(_options(), state="off")
    _, _, attempt, snapshot = await _install_repositories(monkeypatch)

    result = await readiness_ws.async_execution_readiness(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id=attempt.attempt_id,
        plan_value=_plan().as_dict(),
        now=START,
    )

    assert result["attempt"] == attempt.as_dict()
    assert result["action_snapshot"] == snapshot.as_dict()
    assert result["readiness"]["status"] == READINESS_READY
    assert result["readiness"]["action_required"] is True
    assert result["read_only"] is True
    assert result["execution_performed"] is False
    assert result["service_call_performed"] is False
    assert result["executor_available"] is False


@pytest.mark.asyncio
async def test_readiness_endpoint_tampered_plan_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass(_options(), state="off")
    _, _, attempt, _ = await _install_repositories(monkeypatch)
    tampered = _plan().as_dict()
    tampered["average_czk_kwh"] = 2.1
    tampered["estimated_cost_czk"] = 46.2

    result = await readiness_ws.async_execution_readiness(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id=attempt.attempt_id,
        plan_value=tampered,
        now=START,
    )

    assert result["readiness"]["status"] == READINESS_BLOCKED
    assert result["readiness"]["reason"] == REASON_SCOPE_CHANGED
    assert result["readiness"]["approval_scope_matches"] is False
    assert result["service_call_performed"] is False


@pytest.mark.asyncio
async def test_readiness_endpoint_changed_binding_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass(_options(_profile(entity_id="switch.other")), state="off")
    _, _, attempt, _ = await _install_repositories(monkeypatch)

    result = await readiness_ws.async_execution_readiness(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id=attempt.attempt_id,
        plan_value=_plan().as_dict(),
        now=START,
    )

    assert result["readiness"]["status"] == READINESS_BLOCKED
    assert result["readiness"]["reason"] == "profile_or_action_mapping_changed"
    assert result["readiness"]["profile_matches"] is False


@pytest.mark.asyncio
async def test_readiness_endpoint_missing_attempt_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass(_options(), state="off")
    attempt_repository = ExecutionAttemptRepository(_FakeStore())
    snapshot_repository = ExecutionActionSnapshotRepository(_FakeStore())
    monkeypatch.setattr(
        readiness_ws.consume_ws,
        "_attempt_repository",
        lambda hass, entry_id: attempt_repository,
    )
    monkeypatch.setattr(
        readiness_ws,
        "action_snapshot_repository",
        lambda hass, entry_id: snapshot_repository,
    )

    with pytest.raises(ExecutionReadinessError, match="attempt not found"):
        await readiness_ws.async_execution_readiness(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="missing",
            plan_value=_plan().as_dict(),
            now=START,
        )


@pytest.mark.asyncio
async def test_readiness_endpoint_missing_snapshot_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass(_options(), state="off")
    attempt_repository = ExecutionAttemptRepository(_FakeStore())
    snapshot_repository = ExecutionActionSnapshotRepository(_FakeStore())
    attempt = _attempt()
    await attempt_repository.async_record(attempt)
    monkeypatch.setattr(
        readiness_ws.consume_ws,
        "_attempt_repository",
        lambda hass, entry_id: attempt_repository,
    )
    monkeypatch.setattr(
        readiness_ws,
        "action_snapshot_repository",
        lambda hass, entry_id: snapshot_repository,
    )

    with pytest.raises(ExecutionReadinessError, match="snapshot not found"):
        await readiness_ws.async_execution_readiness(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id=attempt.attempt_id,
            plan_value=_plan().as_dict(),
            now=START,
        )
