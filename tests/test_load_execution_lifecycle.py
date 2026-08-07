from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import (
    CALL_CONFIRMED,
    CALL_NOT_STARTED,
    CALL_UNKNOWN,
    STATE_CANCELLED,
    STATE_DISPATCHED,
    STATE_DISPATCHING,
    STATE_FAILED,
    STATE_PREPARED,
    STATE_RECOVERY_REQUIRED,
    STATE_VERIFIED,
    VERIFY_CONFIRMED,
    VERIFY_FAILED,
    VERIFY_PENDING,
    ExecutionLifecycleConflictError,
    ExecutionLifecycleError,
    ExecutionLifecycleLedger,
    ExecutionLifecycleRecord,
    ExecutionLifecycleRepository,
    ExecutionPlanSnapshot,
    begin_dispatch,
    cancel_prepared,
    confirm_dispatch,
    lifecycle_storage_key,
    mark_failed,
    require_recovery_after_restart,
    validate_lifecycle_transition,
    verify_desired_state,
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


def _plan(*, average: float = 2.0) -> LoadPlan:
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
        average_czk_kwh=average,
        minimum_czk_kwh=1.5,
        maximum_czk_kwh=2.5,
        estimated_energy_kwh=energy,
        estimated_cost_czk=energy * average,
    )


def _attempt(*, plan: LoadPlan | None = None) -> ExecutionAttempt:
    current_plan = plan or _plan()
    return ExecutionAttempt(
        attempt_id="attempt-1",
        entry_id="entry-1",
        profile_id="ev-home",
        entity_id="switch.enyaq_charging",
        approval_id="approval-1",
        approval_fingerprint="a" * 64,
        snapshot_digest=execution_snapshot_digest(_profile(), current_plan, _policy()),
        intent="execute_load_plan",
        approval_issued_at=int((START - timedelta(minutes=5)).timestamp()),
        approval_expires_at=int((START + timedelta(minutes=5)).timestamp()),
        created_at=int((START - timedelta(minutes=1)).timestamp()),
    ).validated()


def _snapshot(attempt: ExecutionAttempt | None = None) -> ExecutionActionSnapshot:
    current = attempt or _attempt()
    return ExecutionActionSnapshot.from_attempt_and_intent(
        attempt=current,
        intent=resolve_start_action_intent(_profile()),
        created_at=current.created_at,
    )


def _prepared(*, created_at: int | None = None) -> ExecutionLifecycleRecord:
    plan = _plan()
    attempt = _attempt(plan=plan)
    snapshot = _snapshot(attempt)
    readiness = evaluate_execution_readiness(
        attempt=attempt,
        snapshot=snapshot,
        profile=_profile(),
        plan=plan,
        policy=_policy(),
        current_state="off",
        now=START,
    )
    return ExecutionLifecycleRecord.prepared(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=plan,
        readiness=readiness,
        created_at=created_at if created_at is not None else int(START.timestamp()),
    )


def test_prepared_lifecycle_persists_exact_plan_and_no_call_evidence() -> None:
    record = _prepared()

    assert record.state == STATE_PREPARED
    assert record.service_call_status == CALL_NOT_STARTED
    assert record.verification_status == VERIFY_PENDING
    assert record.plan.to_load_plan() == _plan()
    assert record.plan.digest() == record.plan_digest
    assert record.dispatch_attempts == 0
    assert record.executor_available is False
    assert record.as_dict()["service_call_performed"] is False


def test_prepare_requires_ready_action_required_decision() -> None:
    plan = _plan()
    attempt = _attempt(plan=plan)
    snapshot = _snapshot(attempt)
    waiting = evaluate_execution_readiness(
        attempt=attempt,
        snapshot=snapshot,
        profile=_profile(),
        plan=plan,
        policy=_policy(),
        current_state="off",
        now=START - timedelta(seconds=10),
    )

    with pytest.raises(ExecutionLifecycleError, match="readiness must be ready"):
        ExecutionLifecycleRecord.prepared(
            attempt=attempt,
            action_snapshot=snapshot,
            plan=plan,
            readiness=waiting,
            created_at=int(START.timestamp()),
        )


def test_plan_snapshot_detects_tampered_persisted_plan() -> None:
    raw = ExecutionPlanSnapshot.from_load_plan(_plan()).as_dict()
    raw["estimated_cost_czk"] = 999.0

    with pytest.raises(ExecutionLifecycleError, match="cost is inconsistent"):
        ExecutionPlanSnapshot.from_dict(raw)


def test_begin_dispatch_marks_outcome_unknown_before_future_call() -> None:
    prepared = _prepared()
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)

    assert dispatching.state == STATE_DISPATCHING
    assert dispatching.service_call_status == CALL_UNKNOWN
    assert dispatching.verification_status == VERIFY_PENDING
    assert dispatching.dispatch_attempts == 1
    assert dispatching.dispatch_started_at == prepared.updated_at + 1
    assert dispatching.as_dict()["service_call_performed"] is None


def test_confirm_dispatch_then_verify_desired_state() -> None:
    prepared = _prepared()
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)
    dispatched = confirm_dispatch(dispatching, now=prepared.updated_at + 2)
    verified = verify_desired_state(
        dispatched,
        current_state="on",
        now=prepared.updated_at + 3,
    )

    assert dispatched.state == STATE_DISPATCHED
    assert dispatched.service_call_status == CALL_CONFIRMED
    assert dispatched.as_dict()["service_call_performed"] is True
    assert verified.state == STATE_VERIFIED
    assert verified.verification_status == VERIFY_CONFIRMED
    assert verified.verified_at == prepared.updated_at + 3


def test_interrupted_dispatch_becomes_recovery_required_after_restart() -> None:
    prepared = _prepared()
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)
    recovered = require_recovery_after_restart(
        dispatching,
        now=prepared.updated_at + 10,
    )

    assert recovered.state == STATE_RECOVERY_REQUIRED
    assert recovered.service_call_status == CALL_UNKNOWN
    assert recovered.as_dict()["service_call_performed"] is None


def test_recovery_can_verify_observed_desired_state_without_claiming_call_confirmation() -> None:
    prepared = _prepared()
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)
    recovery = require_recovery_after_restart(dispatching, now=prepared.updated_at + 10)
    verified = verify_desired_state(
        recovery,
        current_state="on",
        now=prepared.updated_at + 11,
    )

    assert verified.state == STATE_VERIFIED
    assert verified.service_call_status == CALL_UNKNOWN
    assert verified.verification_status == VERIFY_CONFIRMED
    assert verified.as_dict()["service_call_performed"] is None


def test_recovery_refuses_to_verify_wrong_entity_state() -> None:
    prepared = _prepared()
    recovery = require_recovery_after_restart(
        begin_dispatch(prepared, now=prepared.updated_at + 1),
        now=prepared.updated_at + 10,
    )

    with pytest.raises(ExecutionLifecycleError, match="does not match desired state"):
        verify_desired_state(
            recovery,
            current_state="off",
            now=prepared.updated_at + 11,
        )


def test_prepared_can_be_cancelled_before_dispatch() -> None:
    prepared = _prepared()
    cancelled = cancel_prepared(prepared, now=prepared.updated_at + 1)

    assert cancelled.state == STATE_CANCELLED
    assert cancelled.service_call_status == CALL_NOT_STARTED
    assert cancelled.dispatch_attempts == 0
    assert cancelled.cancelled_at == prepared.updated_at + 1


def test_dispatching_can_fail_with_unknown_outcome() -> None:
    prepared = _prepared()
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)
    failed = mark_failed(
        dispatching,
        reason="service timeout",
        now=prepared.updated_at + 2,
    )

    assert failed.state == STATE_FAILED
    assert failed.service_call_status == CALL_UNKNOWN
    assert failed.verification_status == VERIFY_FAILED
    assert failed.failure_reason == "service timeout"
    assert failed.as_dict()["service_call_performed"] is None


def test_invalid_state_skip_is_rejected() -> None:
    prepared = _prepared()
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)
    dispatched = confirm_dispatch(dispatching, now=prepared.updated_at + 2)

    with pytest.raises(ExecutionLifecycleError, match="invalid lifecycle transition"):
        validate_lifecycle_transition(prepared, dispatched)


def test_immutable_binding_change_is_rejected() -> None:
    prepared = _prepared()
    changed = replace_record_plan_digest_for_test(prepared)

    with pytest.raises(ExecutionLifecycleError):
        changed.validated()


def replace_record_plan_digest_for_test(record: ExecutionLifecycleRecord) -> ExecutionLifecycleRecord:
    from dataclasses import replace

    return replace(record, plan_digest="0" * 64)


def test_ledger_prepare_is_idempotent_even_if_retry_timestamp_differs() -> None:
    ledger = ExecutionLifecycleLedger()
    first_candidate = _prepared(created_at=int(START.timestamp()))
    retry_candidate = _prepared(created_at=int(START.timestamp()) + 1)

    first = ledger.prepare(first_candidate)
    retry = ledger.prepare(retry_candidate)

    assert first.created is True
    assert retry.created is False
    assert retry.idempotent_replay is True
    assert retry.record == first.record
    assert len(ledger.records) == 1


def test_ledger_rejects_same_attempt_with_different_lifecycle_identity() -> None:
    ledger = ExecutionLifecycleLedger()
    first = _prepared()
    ledger.prepare(first)
    changed = replace_record_lifecycle_identity_for_test(first)

    with pytest.raises(ExecutionLifecycleConflictError):
        ledger.prepare(changed)


def replace_record_lifecycle_identity_for_test(record: ExecutionLifecycleRecord) -> ExecutionLifecycleRecord:
    from dataclasses import replace

    return replace(record, lifecycle_id="0" * 32)


def test_storage_round_trip_preserves_prepared_record() -> None:
    ledger = ExecutionLifecycleLedger()
    record = _prepared()
    ledger.prepare(record)

    restored = ExecutionLifecycleLedger.from_storage(ledger.as_storage())

    assert restored.records == (record,)
    assert restored.get_by_attempt_id(record.attempt_id) == record


@pytest.mark.asyncio
async def test_repository_persists_prepare_once_and_retry_is_idempotent() -> None:
    store = _FakeStore()
    repository = ExecutionLifecycleRepository(store)

    first = await repository.async_prepare(_prepared())
    retry = await repository.async_prepare(_prepared(created_at=int(START.timestamp()) + 1))

    assert first.created is True
    assert retry.idempotent_replay is True
    assert store.saves == 1
    assert len(await repository.async_list()) == 1


@pytest.mark.asyncio
async def test_repository_rolls_back_prepare_when_storage_fails() -> None:
    store = _FakeStore()
    store.fail_save = True
    repository = ExecutionLifecycleRepository(store)

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await repository.async_prepare(_prepared())

    assert await repository.async_list() == ()
    assert store.saves == 0


@pytest.mark.asyncio
async def test_repository_persists_valid_transition_atomically() -> None:
    store = _FakeStore()
    repository = ExecutionLifecycleRepository(store)
    prepared = (await repository.async_prepare(_prepared())).record
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)

    updated = await repository.async_update(dispatching)

    assert updated.state == STATE_DISPATCHING
    assert store.saves == 2
    assert (await repository.async_get_by_attempt_id(prepared.attempt_id)).state == STATE_DISPATCHING


def test_storage_key_is_stable_and_isolated_per_entry() -> None:
    first = lifecycle_storage_key("entry-1")
    again = lifecycle_storage_key("entry-1")
    other = lifecycle_storage_key("entry-2")

    assert first == again
    assert first != other
    assert first.startswith("frakon_energy.load_execution_lifecycle.")
