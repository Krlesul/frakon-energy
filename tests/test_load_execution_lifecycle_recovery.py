from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_lifecycle_recovery as recovery
from custom_components.frakon_energy import load_execution_lifecycle_ws_api as lifecycle_ws
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import (
    STATE_DISPATCHED,
    STATE_DISPATCHING,
    STATE_PREPARED,
    STATE_RECOVERY_REQUIRED,
    ExecutionLifecycleRecord,
    ExecutionLifecycleRepository,
    begin_dispatch,
    confirm_dispatch,
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
        self.fail_save = False
        self.saves = 0

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        if self.fail_save:
            raise RuntimeError("storage unavailable")
        self.data = data
        self.saves += 1


class _FakeHass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}


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


async def _repository_with_state(state: str):
    store = _FakeStore()
    repository = ExecutionLifecycleRepository(store)
    prepared = (await repository.async_prepare(_prepared())).record
    if state == STATE_PREPARED:
        return store, repository, prepared
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)
    await repository.async_update(dispatching)
    if state == STATE_DISPATCHING:
        return store, repository, dispatching
    dispatched = confirm_dispatch(dispatching, now=prepared.updated_at + 2)
    await repository.async_update(dispatched)
    if state == STATE_DISPATCHED:
        return store, repository, dispatched
    raise AssertionError(f"unsupported test state: {state}")


@pytest.mark.asyncio
async def test_empty_startup_recovery_marks_entry_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = _FakeHass()
    store = _FakeStore()
    repository = ExecutionLifecycleRepository(store)
    monkeypatch.setattr(recovery, "lifecycle_repository", lambda hass, entry_id: repository)

    summary = await recovery.async_initialize_lifecycle_recovery(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        now=START,
    )

    assert summary.status == recovery.RECOVERY_OK
    assert summary.scanned == 0
    assert summary.transitioned_to_recovery == 0
    assert summary.recovery_required == 0
    assert summary.executor_available is False
    assert recovery.lifecycle_recovery_summary(hass, "entry-1") == summary  # type: ignore[arg-type]
    recovery.assert_lifecycle_recovery_ready(hass, "entry-1")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_dispatching_is_persistently_converted_to_recovery_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass()
    store, repository, dispatching = await _repository_with_state(STATE_DISPATCHING)
    monkeypatch.setattr(recovery, "lifecycle_repository", lambda hass, entry_id: repository)
    before_saves = store.saves

    summary = await recovery.async_initialize_lifecycle_recovery(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        now=START + timedelta(seconds=10),
    )
    recovered = await repository.async_get_by_attempt_id(dispatching.attempt_id)

    assert summary.status == recovery.RECOVERY_OK
    assert summary.scanned == 1
    assert summary.transitioned_to_recovery == 1
    assert summary.recovery_required == 1
    assert recovered is not None
    assert recovered.state == STATE_RECOVERY_REQUIRED
    assert recovered.service_call_status == "unknown"
    assert recovered.as_dict()["service_call_performed"] is None
    assert store.saves == before_saves + 1


@pytest.mark.asyncio
async def test_confirmed_dispatched_state_is_not_downgraded_to_unknown_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass()
    store, repository, dispatched = await _repository_with_state(STATE_DISPATCHED)
    monkeypatch.setattr(recovery, "lifecycle_repository", lambda hass, entry_id: repository)
    before_saves = store.saves

    summary = await recovery.async_initialize_lifecycle_recovery(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        now=START + timedelta(seconds=10),
    )
    current = await repository.async_get_by_attempt_id(dispatched.attempt_id)

    assert summary.status == recovery.RECOVERY_OK
    assert summary.transitioned_to_recovery == 0
    assert summary.dispatched_pending_verification == 1
    assert current == dispatched
    assert store.saves == before_saves


@pytest.mark.asyncio
async def test_storage_failure_marks_recovery_failed_and_blocks_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass()
    store, repository, dispatching = await _repository_with_state(STATE_DISPATCHING)
    store.fail_save = True
    monkeypatch.setattr(recovery, "lifecycle_repository", lambda hass, entry_id: repository)

    summary = await recovery.async_initialize_lifecycle_recovery(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        now=START + timedelta(seconds=10),
    )
    current = await repository.async_get_by_attempt_id(dispatching.attempt_id)

    assert summary.status == recovery.RECOVERY_FAILED
    assert "storage unavailable" in str(summary.error)
    assert current is not None
    assert current.state == STATE_DISPATCHING
    with pytest.raises(recovery.LifecycleRecoveryBlockedError, match="recovery is failed"):
        recovery.assert_lifecycle_recovery_ready(hass, "entry-1")  # type: ignore[arg-type]


def test_missing_recovery_initialization_is_fail_closed() -> None:
    hass = _FakeHass()

    summary = recovery.lifecycle_recovery_summary(hass, "entry-1")  # type: ignore[arg-type]

    assert summary.status == recovery.RECOVERY_NOT_INITIALIZED
    with pytest.raises(recovery.LifecycleRecoveryBlockedError, match="not_initialized"):
        recovery.assert_lifecycle_recovery_ready(hass, "entry-1")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_prepare_is_blocked_before_recovery_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass()
    store = _FakeStore()
    repository = ExecutionLifecycleRepository(store)
    monkeypatch.setattr(lifecycle_ws, "lifecycle_repository", lambda hass, entry_id: repository)

    with pytest.raises(recovery.LifecycleRecoveryBlockedError, match="not_initialized"):
        await lifecycle_ws.async_prepare_execution_lifecycle(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            plan_value=_plan().as_dict(),
            now=START,
        )

    assert await repository.async_list() == ()


def test_recovery_diagnostic_never_mutates_or_claims_execution() -> None:
    prepared = _prepared()
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)
    record = recovery.require_recovery_after_restart(
        dispatching,
        now=prepared.updated_at + 2,
    )

    diagnostic = recovery.recovery_diagnostic_for_record(
        record,
        current_state="on",
    )

    assert diagnostic["state"] == STATE_RECOVERY_REQUIRED
    assert diagnostic["desired_state_observed"] is True
    assert diagnostic["diagnostic"] == "desired_state_observed_after_unknown_dispatch"
    assert diagnostic["service_call_performed"] is None
    assert diagnostic["read_only"] is True
    assert diagnostic["execution_performed"] is False
    assert diagnostic["executor_available"] is False
