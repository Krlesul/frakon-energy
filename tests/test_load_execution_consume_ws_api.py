from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_approval_ws_api as approval_ws
from custom_components.frakon_energy import load_execution_consume_ws_api as consume_ws
from custom_components.frakon_energy.const import DOMAIN
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_execution_approval import (
    VERIFY_OK,
    VERIFY_REPLAYED,
)
from custom_components.frakon_energy.load_execution_attempt import (
    AttemptConflictError,
    ExecutionAttemptRepository,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    EXECUTION_MODE_DISABLED,
    LoadExecutionPolicy,
    upsert_execution_policy,
)
from custom_components.frakon_energy.load_profiles import (
    PROFILE_KIND_EV,
    LoadProfile,
    upsert_profile,
)

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 8, 7, 18, 30, tzinfo=TZ)


class _FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.fail_save = False
        self.saves = 0

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        if self.fail_save:
            raise RuntimeError("storage unavailable")
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
    def __init__(self, states: dict[str, str]) -> None:
        self._states = states

    def get(self, entity_id: str) -> object | None:
        value = self._states.get(entity_id)
        return SimpleNamespace(state=value) if value is not None else None


class _FakeHass:
    def __init__(self, options: dict[str, Any], states: dict[str, str] | None = None) -> None:
        self.data: dict[str, Any] = {}
        self.config_entries = _FakeConfigEntries(_FakeEntry(options))
        self.states = _FakeStates(states or {"switch.enyaq_charging": "off"})


def _profile() -> LoadProfile:
    return LoadProfile(
        "ev-home",
        "Enyaq",
        PROFILE_KIND_EV,
        120,
        11.0,
        entity_id="switch.enyaq_charging",
    )


def _policy() -> LoadExecutionPolicy:
    return LoadExecutionPolicy(
        "ev-home",
        mode=EXECUTION_MODE_APPROVAL_REQUIRED,
        max_power_kw=11.0,
        max_duration_minutes=120,
    )


def _plan(
    *,
    starts_at: datetime | None = None,
    duration_minutes: int = 120,
) -> LoadPlan:
    starts = starts_at or datetime(2026, 8, 8, 1, 0, tzinfo=TZ)
    ends = starts + timedelta(minutes=duration_minutes)
    power = 11.0
    average = 2.0
    energy = power * duration_minutes / 60
    return LoadPlan(
        load_id="ev-home",
        name="Enyaq",
        starts_at=starts.isoformat(),
        ends_at=ends.isoformat(),
        duration_minutes=duration_minutes,
        interval_count=duration_minutes // 15,
        power_kw=power,
        average_czk_kwh=average,
        minimum_czk_kwh=1.5,
        maximum_czk_kwh=2.5,
        estimated_energy_kwh=energy,
        estimated_cost_czk=energy * average,
    )


def _options() -> dict[str, Any]:
    options: dict[str, Any] = upsert_profile({}, _profile())
    return upsert_execution_policy(options, _policy())


def _authority_and_approval(hass: _FakeHass, plan: LoadPlan | None = None):
    candidate = plan or _plan()
    authority = approval_ws._approval_authority(hass, "entry-1")  # type: ignore[arg-type]
    approval = authority.issue(
        _profile(),
        candidate,
        _policy(),
        entity_available=True,
        now=NOW,
        ttl_seconds=120,
    )
    return authority, approval, candidate


def _repository(monkeypatch: pytest.MonkeyPatch, *, fail_save: bool = False):
    store = _FakeStore()
    store.fail_save = fail_save
    repository = ExecutionAttemptRepository(store)
    monkeypatch.setattr(consume_ws, "_attempt_repository", lambda hass, entry_id: repository)
    return store, repository


@pytest.mark.asyncio
async def test_first_consume_persists_attempt_then_consumes_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass(_options())
    authority, approval, plan = _authority_and_approval(hass)
    store, repository = _repository(monkeypatch)
    consumed_at = NOW + timedelta(seconds=1)

    result = await consume_ws.async_consume_execution_approval(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        profile_id="ev-home",
        approval_value=approval.as_dict(),
        plan_value=plan.as_dict(),
        now=consumed_at,
    )

    assert result["created"] is True
    assert result["idempotent_replay"] is False
    assert result["approval_consumed"] is True
    assert result["execution_performed"] is False
    assert result["executor_available"] is False
    assert store.saves == 1
    assert len(await repository.async_list()) == 1

    replay_check = authority.verify(
        approval,
        _profile(),
        plan,
        _policy(),
        entity_available=True,
        now=consumed_at,
    )
    assert replay_check.valid is False
    assert replay_check.reason == VERIFY_REPLAYED
    assert replay_check.consumed is True


@pytest.mark.asyncio
async def test_identical_retry_returns_existing_attempt_even_after_approval_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass(_options())
    _, approval, plan = _authority_and_approval(hass)
    store, _ = _repository(monkeypatch)

    first = await consume_ws.async_consume_execution_approval(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        profile_id="ev-home",
        approval_value=approval.as_dict(),
        plan_value=plan.as_dict(),
        now=NOW + timedelta(seconds=1),
    )
    retry = await consume_ws.async_consume_execution_approval(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        profile_id="ev-home",
        approval_value=approval.as_dict(),
        plan_value={"ignored": "for exact persisted retry"},
        now=NOW + timedelta(minutes=10),
    )

    assert first["attempt"] == retry["attempt"]
    assert retry["created"] is False
    assert retry["idempotent_replay"] is True
    assert retry["approval_consumed"] is True
    assert retry["execution_performed"] is False
    assert store.saves == 1


@pytest.mark.asyncio
async def test_same_approval_id_with_changed_signature_is_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass(_options())
    _, approval, plan = _authority_and_approval(hass)
    _repository(monkeypatch)
    await consume_ws.async_consume_execution_approval(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        profile_id="ev-home",
        approval_value=approval.as_dict(),
        plan_value=plan.as_dict(),
        now=NOW + timedelta(seconds=1),
    )
    tampered = replace(approval, signature="0" * 64)

    with pytest.raises(AttemptConflictError, match="different artifact or scope"):
        await consume_ws.async_consume_execution_approval(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            profile_id="ev-home",
            approval_value=tampered.as_dict(),
            plan_value=plan.as_dict(),
            now=NOW + timedelta(seconds=2),
        )


@pytest.mark.asyncio
async def test_storage_failure_leaves_approval_unconsumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass(_options())
    authority, approval, plan = _authority_and_approval(hass)
    _, repository = _repository(monkeypatch, fail_save=True)
    consumed_at = NOW + timedelta(seconds=1)

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await consume_ws.async_consume_execution_approval(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            profile_id="ev-home",
            approval_value=approval.as_dict(),
            plan_value=plan.as_dict(),
            now=consumed_at,
        )

    assert await repository.async_list() == ()
    verification = authority.verify(
        approval,
        _profile(),
        plan,
        _policy(),
        entity_available=True,
        now=consumed_at,
    )
    assert verification.valid is True
    assert verification.reason == VERIFY_OK
    assert verification.consumed is False


@pytest.mark.asyncio
async def test_entity_becoming_unavailable_rejects_without_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass(_options(), {"switch.enyaq_charging": "off"})
    _, approval, plan = _authority_and_approval(hass)
    _, repository = _repository(monkeypatch)
    hass.states = _FakeStates({"switch.enyaq_charging": "unavailable"})

    with pytest.raises(consume_ws.ApprovalConsumeError, match="verification failed"):
        await consume_ws.async_consume_execution_approval(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            profile_id="ev-home",
            approval_value=approval.as_dict(),
            plan_value=plan.as_dict(),
            now=NOW + timedelta(seconds=1),
        )

    assert await repository.async_list() == ()


@pytest.mark.asyncio
async def test_policy_change_after_issuance_rejects_without_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass(_options())
    _, approval, plan = _authority_and_approval(hass)
    _, repository = _repository(monkeypatch)
    entry = hass.config_entries.entry
    entry.options = upsert_execution_policy(
        entry.options,
        LoadExecutionPolicy("ev-home", mode=EXECUTION_MODE_DISABLED),
    )

    with pytest.raises(consume_ws.ApprovalConsumeError, match="verification failed"):
        await consume_ws.async_consume_execution_approval(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            profile_id="ev-home",
            approval_value=approval.as_dict(),
            plan_value=plan.as_dict(),
            now=NOW + timedelta(seconds=1),
        )

    assert await repository.async_list() == ()


@pytest.mark.asyncio
async def test_tampered_plan_is_rejected_before_attempt_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass(_options())
    _, approval, plan = _authority_and_approval(hass)
    _, repository = _repository(monkeypatch)
    tampered = plan.as_dict()
    tampered["estimated_cost_czk"] = 999.0

    with pytest.raises(consume_ws.ApprovalConsumeError, match="estimated_cost_czk is inconsistent"):
        await consume_ws.async_consume_execution_approval(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            profile_id="ev-home",
            approval_value=approval.as_dict(),
            plan_value=tampered,
            now=NOW + timedelta(seconds=1),
        )

    assert await repository.async_list() == ()


@pytest.mark.asyncio
async def test_plan_that_already_started_is_rejected_even_if_approval_not_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(starts_at=NOW + timedelta(seconds=30), duration_minutes=15)
    short_policy = LoadExecutionPolicy(
        "ev-home",
        mode=EXECUTION_MODE_APPROVAL_REQUIRED,
        max_power_kw=11.0,
        max_duration_minutes=120,
    )
    options: dict[str, Any] = upsert_profile({}, _profile())
    options = upsert_execution_policy(options, short_policy)
    hass = _FakeHass(options)
    authority = approval_ws._approval_authority(hass, "entry-1")  # type: ignore[arg-type]
    approval = authority.issue(
        _profile(),
        plan,
        short_policy,
        entity_available=True,
        now=NOW,
        ttl_seconds=120,
    )
    _, repository = _repository(monkeypatch)

    with pytest.raises(consume_ws.ApprovalConsumeError, match="already started or is stale"):
        await consume_ws.async_consume_execution_approval(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            profile_id="ev-home",
            approval_value=approval.as_dict(),
            plan_value=plan.as_dict(),
            now=NOW + timedelta(seconds=31),
        )

    assert await repository.async_list() == ()


@pytest.mark.asyncio
async def test_attempt_list_is_read_only_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = _FakeHass(_options())
    _, approval, plan = _authority_and_approval(hass)
    _repository(monkeypatch)
    await consume_ws.async_consume_execution_approval(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        profile_id="ev-home",
        approval_value=approval.as_dict(),
        plan_value=plan.as_dict(),
        now=NOW + timedelta(seconds=1),
    )

    result = await consume_ws.async_list_execution_attempts(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert len(result["attempts"]) == 1
    assert result["execution_performed"] is False
    assert result["executor_available"] is False
