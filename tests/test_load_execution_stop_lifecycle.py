from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import (
    STATE_DISPATCHING,
    ExecutionLifecycleRecord,
    begin_dispatch,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import evaluate_execution_readiness
from custom_components.frakon_energy.load_execution_stop_lease import ExecutionStopLease
from custom_components.frakon_energy.load_execution_stop_lifecycle import (
    STOP_CALL_CONFIRMED,
    STOP_CALL_NOT_STARTED,
    STOP_CALL_UNKNOWN,
    STOP_STATE_DISPATCHED,
    STOP_STATE_DISPATCHING,
    STOP_STATE_FAILED,
    STOP_STATE_OWNED,
    STOP_STATE_RECOVERY_REQUIRED,
    STOP_STATE_SATISFIED,
    STOP_STATE_VERIFIED,
    ExecutionStopLifecycleLedger,
    ExecutionStopLifecycleRecord,
    ExecutionStopLifecycleRepository,
    StopLifecycleConflictError,
    StopLifecycleError,
    begin_stop_dispatch,
    confirm_stop_dispatch,
    fail_stop_lifecycle,
    require_stop_recovery_after_restart,
    satisfy_stop_without_dispatch,
    validate_stop_lifecycle_transition,
    verify_stop_state,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)


class _FakeStore:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = data
        self.saves = 0
        self.fail_save = False

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        if self.fail_save:
            raise RuntimeError("storage unavailable")
        self.data = data
        self.saves += 1


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


def _dispatching_start() -> ExecutionLifecycleRecord:
    prepared = _prepared()
    return begin_dispatch(prepared, now=prepared.updated_at + 1)


def _lease(start_lifecycle: ExecutionLifecycleRecord | None = None) -> ExecutionStopLease:
    lifecycle = start_lifecycle or _prepared()
    if lifecycle.state == STATE_DISPATCHING:
        lifecycle = _prepared()
    return ExecutionStopLease.from_prepared_lifecycle(
        lifecycle,
        created_at=lifecycle.updated_at,
    )


def _owned() -> ExecutionStopLifecycleRecord:
    start_lifecycle = _dispatching_start()
    lease = _lease(start_lifecycle)
    return ExecutionStopLifecycleRecord.owned(
        lease=lease,
        start_lifecycle=start_lifecycle,
        created_at=start_lifecycle.updated_at,
    )


def test_owned_stop_lifecycle_binds_exact_lease_and_start_dispatch() -> None:
    start_lifecycle = _dispatching_start()
    lease = _lease(start_lifecycle)
    record = ExecutionStopLifecycleRecord.owned(
        lease=lease,
        start_lifecycle=start_lifecycle,
        created_at=start_lifecycle.updated_at,
    )

    assert record.state == STOP_STATE_OWNED
    assert record.lease_id == lease.lease_id
    assert record.start_lifecycle_id == start_lifecycle.lifecycle_id
    assert record.entity_id == start_lifecycle.entity_id
    assert record.service_domain == "switch"
    assert record.service_name == "turn_off"
    assert record.desired_state == "off"
    assert record.service_call_status == STOP_CALL_NOT_STARTED
    assert record.dispatch_attempts == 0
    assert record.as_dict()["service_call_performed"] is False


def test_ownership_rejects_start_that_has_not_entered_dispatching() -> None:
    prepared = _prepared()
    lease = _lease(prepared)

    with pytest.raises(StopLifecycleError, match="must be dispatching"):
        ExecutionStopLifecycleRecord.owned(
            lease=lease,
            start_lifecycle=prepared,
            created_at=prepared.updated_at,
        )


def test_ownership_rejects_tampered_lease_scope() -> None:
    start_lifecycle = _dispatching_start()
    lease = _lease(start_lifecycle)
    value = lease.as_dict()
    value["attempt_id"] = "other-attempt"
    # Recompute is deliberately impossible through public API; persisted tamper must fail first.
    with pytest.raises(ValueError):
        ExecutionStopLease.from_dict(value)


def test_stop_dispatch_recovery_preserves_unknown_outcome() -> None:
    owned = _owned()
    dispatching = begin_stop_dispatch(owned, now=owned.updated_at + 1)
    recovered = require_stop_recovery_after_restart(
        dispatching,
        now=dispatching.updated_at + 1,
    )

    assert dispatching.state == STOP_STATE_DISPATCHING
    assert dispatching.service_call_status == STOP_CALL_UNKNOWN
    assert dispatching.dispatch_attempts == 1
    assert recovered.state == STOP_STATE_RECOVERY_REQUIRED
    assert recovered.service_call_status == STOP_CALL_UNKNOWN
    assert recovered.as_dict()["service_call_performed"] is None


def test_confirmed_stop_then_live_off_can_be_verified() -> None:
    owned = _owned()
    dispatching = begin_stop_dispatch(owned, now=owned.updated_at + 1)
    dispatched = confirm_stop_dispatch(dispatching, now=dispatching.updated_at + 1)
    verified = verify_stop_state(
        dispatched,
        current_state="off",
        now=dispatched.updated_at + 1,
    )

    assert dispatched.state == STOP_STATE_DISPATCHED
    assert dispatched.service_call_status == STOP_CALL_CONFIRMED
    assert verified.state == STOP_STATE_VERIFIED
    assert verified.service_call_status == STOP_CALL_CONFIRMED
    assert verified.as_dict()["service_call_performed"] is True


def test_unknown_stop_outcome_can_be_verified_without_claiming_call_success() -> None:
    owned = _owned()
    dispatching = begin_stop_dispatch(owned, now=owned.updated_at + 1)
    recovered = require_stop_recovery_after_restart(
        dispatching,
        now=dispatching.updated_at + 1,
    )
    verified = verify_stop_state(
        recovered,
        current_state="off",
        now=recovered.updated_at + 1,
    )

    assert verified.state == STOP_STATE_VERIFIED
    assert verified.service_call_status == STOP_CALL_UNKNOWN
    assert verified.as_dict()["service_call_performed"] is None


def test_owned_stop_already_off_completes_without_dispatch() -> None:
    owned = _owned()
    satisfied = satisfy_stop_without_dispatch(
        owned,
        current_state="off",
        now=owned.updated_at + 1,
    )

    assert satisfied.state == STOP_STATE_SATISFIED
    assert satisfied.service_call_status == STOP_CALL_NOT_STARTED
    assert satisfied.dispatch_attempts == 0
    assert satisfied.as_dict()["service_call_performed"] is False


def test_noop_stop_rejects_entity_that_is_still_on() -> None:
    owned = _owned()
    with pytest.raises(StopLifecycleError, match="not already"):
        satisfy_stop_without_dispatch(
            owned,
            current_state="on",
            now=owned.updated_at + 1,
        )


def test_transition_graph_rejects_skipping_dispatching() -> None:
    owned = _owned()
    impossible = fail_stop_lifecycle(
        owned,
        reason="test failure",
        now=owned.updated_at + 1,
    )
    assert impossible.state == STOP_STATE_FAILED

    dispatching = begin_stop_dispatch(owned, now=owned.updated_at + 1)
    dispatched = confirm_stop_dispatch(dispatching, now=dispatching.updated_at + 1)
    with pytest.raises(StopLifecycleError, match="invalid stop lifecycle transition"):
        validate_stop_lifecycle_transition(owned, dispatched)


def test_transition_rejects_immutable_scope_change() -> None:
    from dataclasses import replace

    owned = _owned()
    changed = replace(owned, entity_id="switch.other")
    with pytest.raises((StopLifecycleError, StopLifecycleConflictError)):
        validate_stop_lifecycle_transition(owned, changed)


def test_storage_round_trip_rejects_tampered_identity() -> None:
    record = _owned()
    storage = ExecutionStopLifecycleLedger((record,)).as_storage()
    storage["records"][0]["stop_lifecycle_id"] = "0" * 32

    with pytest.raises(StopLifecycleError, match="identity does not match"):
        ExecutionStopLifecycleLedger.from_storage(storage)


@pytest.mark.asyncio
async def test_repository_create_is_idempotent_and_persistent() -> None:
    store = _FakeStore()
    repository = ExecutionStopLifecycleRepository(store)
    record = _owned()

    first = await repository.async_create_owned(record)
    second = await repository.async_create_owned(record)
    reloaded = ExecutionStopLifecycleRepository(_FakeStore(store.data))
    loaded = await reloaded.async_get_by_start_lifecycle_id(record.start_lifecycle_id)

    assert first.created is True
    assert second.created is False
    assert second.idempotent_replay is True
    assert store.saves == 1
    assert loaded == record


@pytest.mark.asyncio
async def test_repository_conflicts_on_different_stop_lifecycle_for_same_start() -> None:
    from dataclasses import replace

    store = _FakeStore()
    repository = ExecutionStopLifecycleRepository(store)
    record = _owned()
    await repository.async_create_owned(record)
    # A changed lease id produces a different stop lifecycle identity but remains invalid itself;
    # repository must never accept a rebinding.
    conflicting = replace(record, lease_id="1" * 32, stop_lifecycle_id="2" * 32)
    with pytest.raises(StopLifecycleError):
        await repository.async_create_owned(conflicting)


@pytest.mark.asyncio
async def test_repository_save_failure_rolls_back_in_memory_state() -> None:
    store = _FakeStore()
    repository = ExecutionStopLifecycleRepository(store)
    record = _owned()
    store.fail_save = True

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await repository.async_create_owned(record)

    assert await repository.async_list() == ()
    assert store.data is None


@pytest.mark.asyncio
async def test_repository_update_failure_rolls_back_previous_record() -> None:
    store = _FakeStore()
    repository = ExecutionStopLifecycleRepository(store)
    owned = _owned()
    await repository.async_create_owned(owned)
    dispatching = begin_stop_dispatch(owned, now=owned.updated_at + 1)
    store.fail_save = True

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await repository.async_update(dispatching)

    current = await repository.async_get_by_start_lifecycle_id(owned.start_lifecycle_id)
    assert current == owned
