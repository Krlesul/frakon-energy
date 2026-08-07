from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_lifecycle_recovery_ws_api as recovery_ws
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import (
    STATE_PREPARED,
    STATE_RECOVERY_REQUIRED,
    STATE_VERIFIED,
    ExecutionLifecycleRecord,
    ExecutionLifecycleRepository,
    begin_dispatch,
    require_recovery_after_restart,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import evaluate_execution_readiness
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)


class _FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.saves = 0
        self.fail_save = False

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        if self.fail_save:
            raise RuntimeError("storage unavailable")
        self.data = data
        self.saves += 1


class _FakeStates:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def get(self, entity_id: str) -> object | None:
        return SimpleNamespace(state=self.value) if self.value is not None else None


class _FakeHass:
    def __init__(self, state: str | None) -> None:
        self.data: dict[str, Any] = {}
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
    energy = power * duration / 60
    return LoadPlan(
        load_id="ev-home",
        name="Enyaq",
        starts_at=START.isoformat(),
        ends_at=(START + timedelta(minutes=duration)).isoformat(),
        duration_minutes=duration,
        interval_count=8,
        power_kw=power,
        average_czk_kwh=2.0,
        minimum_czk_kwh=1.5,
        maximum_czk_kwh=2.5,
        estimated_energy_kwh=energy,
        estimated_cost_czk=energy * 2.0,
    )


def _prepared() -> ExecutionLifecycleRecord:
    profile = _profile()
    policy = _policy()
    plan = _plan()
    attempt = ExecutionAttempt(
        attempt_id="attempt-1",
        entry_id="entry-1",
        profile_id=profile.profile_id,
        entity_id=profile.entity_id,
        approval_id="approval-1",
        approval_fingerprint="a" * 64,
        snapshot_digest=execution_snapshot_digest(profile, plan, policy),
        intent="execute_load_plan",
        approval_issued_at=int((START - timedelta(minutes=5)).timestamp()),
        approval_expires_at=int((START + timedelta(minutes=5)).timestamp()),
        created_at=int((START - timedelta(minutes=1)).timestamp()),
    ).validated()
    snapshot = ExecutionActionSnapshot.from_attempt_and_intent(
        attempt=attempt,
        intent=resolve_start_action_intent(profile),
        created_at=attempt.created_at,
    )
    readiness = evaluate_execution_readiness(
        attempt=attempt,
        snapshot=snapshot,
        profile=profile,
        plan=plan,
        policy=policy,
        current_state="off",
        now=START,
    )
    return ExecutionLifecycleRecord.prepared(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=plan,
        readiness=readiness,
        created_at=int(START.timestamp()),
    )


async def _repository_with_record(state: str):
    store = _FakeStore()
    repository = ExecutionLifecycleRepository(store)
    prepared = (await repository.async_prepare(_prepared())).record
    if state == STATE_PREPARED:
        return store, repository, prepared
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)
    await repository.async_update(dispatching)
    recovered = require_recovery_after_restart(
        dispatching,
        now=prepared.updated_at + 2,
    )
    await repository.async_update(recovered)
    if state == STATE_RECOVERY_REQUIRED:
        return store, repository, recovered
    raise AssertionError(state)


def _install(
    monkeypatch: pytest.MonkeyPatch,
    repository: ExecutionLifecycleRepository,
    *,
    recovery_ready: bool = True,
) -> None:
    monkeypatch.setattr(
        recovery_ws,
        "lifecycle_repository",
        lambda hass, entry_id: repository,
    )
    if recovery_ready:
        monkeypatch.setattr(
            recovery_ws,
            "assert_lifecycle_recovery_ready",
            lambda hass, entry_id: None,
        )


@pytest.mark.asyncio
async def test_manual_recovery_verify_transitions_only_when_desired_state_is_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass("on")
    store, repository, recovered = await _repository_with_record(STATE_RECOVERY_REQUIRED)
    _install(monkeypatch, repository)
    before_saves = store.saves

    result = await recovery_ws.async_verify_recovery_lifecycle(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id=recovered.attempt_id,
        now=START + timedelta(seconds=10),
    )
    current = await repository.async_get_by_attempt_id(recovered.attempt_id)

    assert current is not None
    assert current.state == STATE_VERIFIED
    assert current.service_call_status == "unknown"
    assert result["desired_state_observed"] is True
    assert result["recovery_resolved"] is True
    assert result["state_transition_performed"] is True
    assert result["idempotent_replay"] is False
    assert result["service_call_performed"] is None
    assert result["execution_performed"] is False
    assert result["executor_available"] is False
    assert store.saves == before_saves + 1


@pytest.mark.asyncio
async def test_manual_recovery_verify_rejects_live_state_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass("off")
    _, repository, recovered = await _repository_with_record(STATE_RECOVERY_REQUIRED)
    _install(monkeypatch, repository)

    with pytest.raises(recovery_ws.RecoveryResolutionError, match="does not match"):
        await recovery_ws.async_verify_recovery_lifecycle(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id=recovered.attempt_id,
            now=START + timedelta(seconds=10),
        )

    current = await repository.async_get_by_attempt_id(recovered.attempt_id)
    assert current is not None
    assert current.state == STATE_RECOVERY_REQUIRED


@pytest.mark.asyncio
async def test_manual_recovery_verify_rolls_back_when_store_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass("on")
    store, repository, recovered = await _repository_with_record(STATE_RECOVERY_REQUIRED)
    _install(monkeypatch, repository)
    store.fail_save = True

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await recovery_ws.async_verify_recovery_lifecycle(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id=recovered.attempt_id,
            now=START + timedelta(seconds=10),
        )

    current = await repository.async_get_by_attempt_id(recovered.attempt_id)
    assert current is not None
    assert current.state == STATE_RECOVERY_REQUIRED


@pytest.mark.asyncio
async def test_manual_recovery_verify_retry_is_inert_and_keeps_unknown_call_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass("on")
    store, repository, recovered = await _repository_with_record(STATE_RECOVERY_REQUIRED)
    _install(monkeypatch, repository)

    first = await recovery_ws.async_verify_recovery_lifecycle(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id=recovered.attempt_id,
        now=START + timedelta(seconds=10),
    )
    saves_after_first = store.saves
    hass.states.value = "off"
    retry = await recovery_ws.async_verify_recovery_lifecycle(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id=recovered.attempt_id,
        now=START + timedelta(minutes=1),
    )

    assert first["lifecycle"]["state"] == STATE_VERIFIED
    assert retry["lifecycle"] == first["lifecycle"]
    assert retry["desired_state_observed"] is False
    assert retry["state_transition_performed"] is False
    assert retry["idempotent_replay"] is True
    assert retry["service_call_performed"] is None
    assert retry["execution_performed"] is False
    assert store.saves == saves_after_first


@pytest.mark.asyncio
async def test_manual_recovery_verify_rejects_non_recovery_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass("on")
    _, repository, prepared = await _repository_with_record(STATE_PREPARED)
    _install(monkeypatch, repository)

    with pytest.raises(recovery_ws.RecoveryResolutionError, match="not recovery_required"):
        await recovery_ws.async_verify_recovery_lifecycle(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id=prepared.attempt_id,
            now=START + timedelta(seconds=10),
        )


@pytest.mark.asyncio
async def test_manual_recovery_verify_is_blocked_without_startup_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass("on")
    _, repository, recovered = await _repository_with_record(STATE_RECOVERY_REQUIRED)
    _install(monkeypatch, repository, recovery_ready=False)

    with pytest.raises(Exception, match="not_initialized"):
        await recovery_ws.async_verify_recovery_lifecycle(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id=recovered.attempt_id,
            now=START + timedelta(seconds=10),
        )
