from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_dispatch_gate_ws_api as gate_ws
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
from custom_components.frakon_energy.load_execution_dispatch_gate import (
    DISPATCH_GATE_ALREADY_SATISFIED,
    DISPATCH_GATE_BLOCKED,
    DISPATCH_GATE_READY,
    REASON_LIFECYCLE_NOT_PREPARED,
)
from custom_components.frakon_energy.load_execution_lifecycle import (
    ExecutionLifecycleRecord,
    ExecutionLifecycleRepository,
    begin_dispatch,
)
from custom_components.frakon_energy.load_execution_lifecycle_recovery import (
    LifecycleRecoveryBlockedError,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    EXECUTION_MODE_DISABLED,
    LoadExecutionPolicy,
    upsert_execution_policy,
)
from custom_components.frakon_energy.load_execution_readiness import (
    REASON_POLICY_NOT_ELIGIBLE,
    evaluate_execution_readiness,
)
from custom_components.frakon_energy.load_profiles import (
    PROFILE_KIND_EV,
    LoadProfile,
    upsert_profile,
)
from custom_components.frakon_energy.const import DOMAIN

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)


class _FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.saves = 0

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = data
        self.saves += 1


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
    def __init__(self, state: str | None) -> None:
        self.state = state

    def get(self, entity_id: str) -> object | None:
        return SimpleNamespace(state=self.state) if self.state is not None else None


class _FakeHass:
    def __init__(self, options: dict[str, Any], state: str | None = "off") -> None:
        self.data: dict[str, Any] = {}
        self.config_entries = _FakeConfigEntries(_FakeEntry(options))
        self.states = _FakeStates(state)


def _profile() -> LoadProfile:
    return LoadProfile(
        "ev-home",
        "Enyaq",
        PROFILE_KIND_EV,
        120,
        11.0,
        entity_id="switch.enyaq_charging",
    )


def _policy(*, mode: str = EXECUTION_MODE_APPROVAL_REQUIRED) -> LoadExecutionPolicy:
    if mode == EXECUTION_MODE_DISABLED:
        return LoadExecutionPolicy("ev-home", mode=mode)
    return LoadExecutionPolicy(
        "ev-home",
        mode=mode,
        max_power_kw=11.0,
        max_duration_minutes=120,
    )


def _options() -> dict[str, Any]:
    options: dict[str, Any] = upsert_profile({}, _profile())
    return upsert_execution_policy(options, _policy())


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


async def _repositories():
    attempt_store = _FakeStore()
    snapshot_store = _FakeStore()
    lifecycle_store = _FakeStore()
    attempts = ExecutionAttemptRepository(attempt_store)
    snapshots = ExecutionActionSnapshotRepository(snapshot_store)
    lifecycles = ExecutionLifecycleRepository(lifecycle_store)

    attempt = _attempt()
    snapshot = _snapshot(attempt)
    readiness = evaluate_execution_readiness(
        attempt=attempt,
        snapshot=snapshot,
        profile=_profile(),
        plan=_plan(),
        policy=_policy(),
        current_state="off",
        now=START,
    )
    lifecycle = ExecutionLifecycleRecord.prepared(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=_plan(),
        readiness=readiness,
        created_at=int(START.timestamp()),
    )
    await attempts.async_record(attempt)
    await snapshots.async_record(snapshot)
    await lifecycles.async_prepare(lifecycle)
    return (
        attempt_store,
        snapshot_store,
        lifecycle_store,
        attempts,
        snapshots,
        lifecycles,
    )


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    attempts: ExecutionAttemptRepository,
    snapshots: ExecutionActionSnapshotRepository,
    lifecycles: ExecutionLifecycleRepository,
) -> None:
    monkeypatch.setattr(
        gate_ws.consume_ws,
        "_attempt_repository",
        lambda hass, entry_id: attempts,
    )
    monkeypatch.setattr(
        gate_ws,
        "action_snapshot_repository",
        lambda hass, entry_id: snapshots,
    )
    monkeypatch.setattr(
        gate_ws,
        "lifecycle_repository",
        lambda hass, entry_id: lifecycles,
    )
    monkeypatch.setattr(
        gate_ws,
        "assert_lifecycle_recovery_ready",
        lambda hass, entry_id: None,
    )


@pytest.mark.asyncio
async def test_dispatch_gate_uses_persisted_plan_and_does_not_mutate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        attempt_store,
        snapshot_store,
        lifecycle_store,
        attempts,
        snapshots,
        lifecycles,
    ) = await _repositories()
    _wire(monkeypatch, attempts, snapshots, lifecycles)
    hass = _FakeHass(_options(), "off")
    before_saves = (attempt_store.saves, snapshot_store.saves, lifecycle_store.saves)

    result = await gate_ws.async_execution_dispatch_gate(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START,
    )

    assert result["dispatch_gate"]["status"] == DISPATCH_GATE_READY
    assert result["dispatch_gate"]["can_dispatch"] is True
    assert result["dispatch_gate"]["can_redispatch"] is False
    assert result["plan"] == _plan().as_dict()
    assert result["read_only"] is True
    assert result["state_transition_performed"] is False
    assert result["service_call_performed"] is False
    assert result["execution_performed"] is False
    assert result["executor_available"] is False
    assert (attempt_store.saves, snapshot_store.saves, lifecycle_store.saves) == before_saves


@pytest.mark.asyncio
async def test_dispatch_gate_returns_noop_when_entity_already_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, _, attempts, snapshots, lifecycles = await _repositories()
    _wire(monkeypatch, attempts, snapshots, lifecycles)
    hass = _FakeHass(_options(), "on")

    result = await gate_ws.async_execution_dispatch_gate(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START,
    )

    assert result["dispatch_gate"]["status"] == DISPATCH_GATE_ALREADY_SATISFIED
    assert result["dispatch_gate"]["can_dispatch"] is False
    assert result["dispatch_gate"]["can_redispatch"] is False


@pytest.mark.asyncio
async def test_current_policy_change_blocks_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, _, attempts, snapshots, lifecycles = await _repositories()
    _wire(monkeypatch, attempts, snapshots, lifecycles)
    options = upsert_execution_policy(_options(), _policy(mode=EXECUTION_MODE_DISABLED))
    hass = _FakeHass(options, "off")

    result = await gate_ws.async_execution_dispatch_gate(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START,
    )

    assert result["readiness"]["reason"] == REASON_POLICY_NOT_ELIGIBLE
    assert result["dispatch_gate"]["status"] == DISPATCH_GATE_BLOCKED
    assert result["dispatch_gate"]["can_dispatch"] is False


@pytest.mark.asyncio
async def test_non_prepared_lifecycle_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, lifecycle_store, attempts, snapshots, lifecycles = await _repositories()
    prepared = await lifecycles.async_get_by_attempt_id("attempt-1")
    assert prepared is not None
    await lifecycles.async_update(begin_dispatch(prepared, now=prepared.updated_at + 1))
    before_saves = lifecycle_store.saves
    _wire(monkeypatch, attempts, snapshots, lifecycles)
    hass = _FakeHass(_options(), "off")

    result = await gate_ws.async_execution_dispatch_gate(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START,
    )

    assert result["dispatch_gate"]["status"] == DISPATCH_GATE_BLOCKED
    assert result["dispatch_gate"]["reason"] == REASON_LIFECYCLE_NOT_PREPARED
    assert result["dispatch_gate"]["can_dispatch"] is False
    assert lifecycle_store.saves == before_saves


@pytest.mark.asyncio
async def test_startup_recovery_gate_blocks_dispatch_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass(_options(), "off")

    def blocked(hass, entry_id):
        raise LifecycleRecoveryBlockedError("execution lifecycle recovery is failed")

    monkeypatch.setattr(gate_ws, "assert_lifecycle_recovery_ready", blocked)

    with pytest.raises(LifecycleRecoveryBlockedError, match="recovery is failed"):
        await gate_ws.async_execution_dispatch_gate(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START,
        )
