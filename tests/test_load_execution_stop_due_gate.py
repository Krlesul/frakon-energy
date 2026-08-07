from dataclasses import replace
from datetime import datetime, timedelta, timezone

from custom_components.frakon_energy.load_execution_stop_due_gate import (
    STOP_DUE_ALREADY_OFF,
    STOP_DUE_BLOCKED,
    STOP_DUE_COMPLETED,
    STOP_DUE_READY,
    STOP_DUE_RECOVERY_REVIEW,
    STOP_DUE_SAFE_TO_VERIFY,
    STOP_DUE_WAITING,
    evaluate_stop_due_gate,
)
from custom_components.frakon_energy.load_execution_stop_lifecycle import (
    STOP_CALL_CONFIRMED,
    STOP_CALL_UNKNOWN,
    STOP_STATE_DISPATCHED,
    STOP_STATE_FAILED,
    STOP_STATE_RECOVERY_REQUIRED,
    STOP_STATE_SATISFIED,
    STOP_STATE_VERIFIED,
    STOP_VERIFY_CONFIRMED,
    STOP_VERIFY_FAILED,
    ExecutionStopLifecycleRecord,
)

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)
END = START + timedelta(hours=2)


def _owned() -> ExecutionStopLifecycleRecord:
    return ExecutionStopLifecycleRecord(
        stop_lifecycle_id="e" * 32,
        lease_id="a" * 32,
        entry_id="entry-1",
        start_lifecycle_id="b" * 32,
        attempt_id="attempt-1",
        action_snapshot_id="c" * 32,
        profile_id="ev-home",
        entity_id="switch.enyaq_charging",
        approval_snapshot_digest="d" * 64,
        plan_digest="f" * 64,
        starts_at=START.isoformat(),
        ends_at=END.isoformat(),
        service_domain="switch",
        service_name="turn_off",
        desired_state="off",
        state="owned",
        service_call_status="not_started",
        verification_status="pending",
        created_at=int(START.timestamp()),
        updated_at=int(START.timestamp()),
    ).validated()


def test_owned_waits_strictly_before_deadline() -> None:
    decision = evaluate_stop_due_gate(
        record=_owned(),
        current_state="on",
        now=END - timedelta(seconds=1),
        recovery_ready=True,
    )
    assert decision.status == STOP_DUE_WAITING
    assert decision.can_dispatch_stop is False
    assert decision.seconds_until_due == 1


def test_owned_becomes_ready_exactly_at_deadline_when_entity_on() -> None:
    decision = evaluate_stop_due_gate(
        record=_owned(),
        current_state="on",
        now=END,
        recovery_ready=True,
    )
    assert decision.status == STOP_DUE_READY
    assert decision.can_dispatch_stop is True
    assert decision.seconds_until_due == 0
    assert decision.can_retry_unknown is False


def test_owned_already_off_is_no_dispatch_completion() -> None:
    decision = evaluate_stop_due_gate(
        record=_owned(),
        current_state="off",
        now=END,
        recovery_ready=True,
    )
    assert decision.status == STOP_DUE_ALREADY_OFF
    assert decision.can_complete_noop is True
    assert decision.can_dispatch_stop is False


def test_unavailable_entity_blocks_even_after_deadline() -> None:
    for state in (None, "unknown", "unavailable"):
        decision = evaluate_stop_due_gate(
            record=_owned(),
            current_state=state,
            now=END + timedelta(minutes=5),
            recovery_ready=True,
        )
        assert decision.status == STOP_DUE_BLOCKED
        assert decision.can_dispatch_stop is False


def test_unhealthy_startup_recovery_blocks_due_stop() -> None:
    decision = evaluate_stop_due_gate(
        record=_owned(),
        current_state="on",
        now=END,
        recovery_ready=False,
    )
    assert decision.status == STOP_DUE_BLOCKED
    assert decision.can_dispatch_stop is False


def test_recovery_required_off_is_safe_to_verify_without_retry() -> None:
    record = replace(
        _owned(),
        state=STOP_STATE_RECOVERY_REQUIRED,
        service_call_status=STOP_CALL_UNKNOWN,
        dispatch_attempts=1,
        dispatch_started_at=int(END.timestamp()),
        updated_at=int(END.timestamp()),
    ).validated()
    decision = evaluate_stop_due_gate(
        record=record,
        current_state="off",
        now=END + timedelta(seconds=10),
        recovery_ready=True,
    )
    assert decision.status == STOP_DUE_SAFE_TO_VERIFY
    assert decision.can_mark_verified is True
    assert decision.can_retry_unknown is False


def test_recovery_required_on_demands_review_not_auto_retry() -> None:
    record = replace(
        _owned(),
        state=STOP_STATE_RECOVERY_REQUIRED,
        service_call_status=STOP_CALL_UNKNOWN,
        dispatch_attempts=1,
        dispatch_started_at=int(END.timestamp()),
        updated_at=int(END.timestamp()),
    ).validated()
    decision = evaluate_stop_due_gate(
        record=record,
        current_state="on",
        now=END + timedelta(seconds=10),
        recovery_ready=True,
    )
    assert decision.status == STOP_DUE_RECOVERY_REVIEW
    assert decision.can_dispatch_stop is False
    assert decision.can_retry_unknown is False


def test_confirmed_dispatch_off_is_safe_to_verify() -> None:
    record = replace(
        _owned(),
        state=STOP_STATE_DISPATCHED,
        service_call_status=STOP_CALL_CONFIRMED,
        dispatch_attempts=1,
        dispatch_started_at=int(END.timestamp()),
        dispatch_confirmed_at=int(END.timestamp()) + 1,
        updated_at=int(END.timestamp()) + 1,
    ).validated()
    decision = evaluate_stop_due_gate(
        record=record,
        current_state="off",
        now=END + timedelta(seconds=2),
        recovery_ready=True,
    )
    assert decision.status == STOP_DUE_SAFE_TO_VERIFY
    assert decision.can_mark_verified is True


def test_confirmed_dispatch_on_is_blocked_not_retried() -> None:
    record = replace(
        _owned(),
        state=STOP_STATE_DISPATCHED,
        service_call_status=STOP_CALL_CONFIRMED,
        dispatch_attempts=1,
        dispatch_started_at=int(END.timestamp()),
        dispatch_confirmed_at=int(END.timestamp()) + 1,
        updated_at=int(END.timestamp()) + 1,
    ).validated()
    decision = evaluate_stop_due_gate(
        record=record,
        current_state="on",
        now=END + timedelta(seconds=2),
        recovery_ready=True,
    )
    assert decision.status == STOP_DUE_BLOCKED
    assert decision.can_dispatch_stop is False


def test_verified_and_satisfied_are_terminal_completed() -> None:
    verified = replace(
        _owned(),
        state=STOP_STATE_VERIFIED,
        service_call_status=STOP_CALL_UNKNOWN,
        verification_status=STOP_VERIFY_CONFIRMED,
        dispatch_attempts=1,
        dispatch_started_at=int(END.timestamp()),
        verified_at=int(END.timestamp()) + 1,
        updated_at=int(END.timestamp()) + 1,
    ).validated()
    satisfied = replace(
        _owned(),
        state=STOP_STATE_SATISFIED,
        verification_status=STOP_VERIFY_CONFIRMED,
        satisfied_at=int(END.timestamp()),
        updated_at=int(END.timestamp()),
    ).validated()
    for record in (verified, satisfied):
        decision = evaluate_stop_due_gate(
            record=record,
            current_state="off",
            now=END + timedelta(minutes=1),
            recovery_ready=True,
        )
        assert decision.status == STOP_DUE_COMPLETED
        assert decision.can_dispatch_stop is False


def test_failed_stop_is_blocked() -> None:
    failed = replace(
        _owned(),
        state=STOP_STATE_FAILED,
        verification_status=STOP_VERIFY_FAILED,
        failed_at=int(END.timestamp()),
        failure_reason="stop failed",
        updated_at=int(END.timestamp()),
    ).validated()
    decision = evaluate_stop_due_gate(
        record=failed,
        current_state="on",
        now=END,
        recovery_ready=True,
    )
    assert decision.status == STOP_DUE_BLOCKED
