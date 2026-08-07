from datetime import datetime, timedelta, timezone

import pytest

from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import (
    CALL_CONFIRMED,
    CALL_UNKNOWN,
    STATE_CANCELLED,
    STATE_DISPATCHED,
    STATE_DISPATCHING,
    STATE_FAILED,
    STATE_PREPARED,
    STATE_RECOVERY_REQUIRED,
    STATE_VERIFIED,
    ExecutionLifecycleRecord,
    begin_dispatch,
    cancel_prepared,
    confirm_dispatch,
    mark_failed,
    require_recovery_after_restart,
    verify_desired_state,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import evaluate_execution_readiness
from custom_components.frakon_energy.load_execution_recovery_resolution import (
    REASON_ALREADY_VERIFIED,
    REASON_DESIRED_STATE_NOT_OBSERVED,
    REASON_DESIRED_STATE_OBSERVED,
    REASON_ENTITY_STATE_UNAVAILABLE,
    REASON_INTERRUPTED_DISPATCH_NOT_RECOVERED,
    REASON_PREPARED_NOT_DISPATCHED,
    REASON_TERMINAL_WITHOUT_VERIFICATION,
    RESOLUTION_BLOCKED,
    RESOLUTION_NOT_APPLICABLE,
    RESOLUTION_OPERATOR_ACTION_REQUIRED,
    RESOLUTION_SAFE_TO_VERIFY,
    evaluate_recovery_resolution,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)


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


def _dispatching() -> ExecutionLifecycleRecord:
    prepared = _prepared()
    return begin_dispatch(prepared, now=prepared.updated_at + 1)


def _recovery_required() -> ExecutionLifecycleRecord:
    dispatching = _dispatching()
    return require_recovery_after_restart(
        dispatching,
        now=dispatching.updated_at + 1,
    )


def _dispatched() -> ExecutionLifecycleRecord:
    dispatching = _dispatching()
    return confirm_dispatch(dispatching, now=dispatching.updated_at + 1)


def test_unknown_outcome_with_desired_state_is_safe_only_to_verify() -> None:
    record = _recovery_required()

    decision = evaluate_recovery_resolution(record, current_state="on")

    assert decision.status == RESOLUTION_SAFE_TO_VERIFY
    assert decision.reason == REASON_DESIRED_STATE_OBSERVED
    assert decision.lifecycle_state == STATE_RECOVERY_REQUIRED
    assert decision.service_call_status == CALL_UNKNOWN
    assert decision.can_mark_verified is True
    assert decision.can_redispatch is False
    assert decision.manual_review_required is False
    assert decision.resolution_performed is False
    assert decision.state_transition_performed is False
    assert decision.execution_performed is False
    assert decision.executor_available is False


def test_confirmed_dispatch_with_desired_state_is_safe_to_verify() -> None:
    record = _dispatched()

    decision = evaluate_recovery_resolution(record, current_state=" ON ")

    assert decision.status == RESOLUTION_SAFE_TO_VERIFY
    assert decision.reason == REASON_DESIRED_STATE_OBSERVED
    assert decision.lifecycle_state == STATE_DISPATCHED
    assert decision.service_call_status == CALL_CONFIRMED
    assert decision.current_state == "on"
    assert decision.can_mark_verified is True
    assert decision.can_redispatch is False


def test_desired_state_not_observed_requires_operator_action_without_redispatch() -> None:
    record = _recovery_required()

    decision = evaluate_recovery_resolution(record, current_state="off")

    assert decision.status == RESOLUTION_OPERATOR_ACTION_REQUIRED
    assert decision.reason == REASON_DESIRED_STATE_NOT_OBSERVED
    assert decision.can_mark_verified is False
    assert decision.can_redispatch is False
    assert decision.manual_review_required is True


@pytest.mark.parametrize("state", [None, "unknown", "unavailable", "   "])
def test_unavailable_live_state_blocks_resolution(state: str | None) -> None:
    decision = evaluate_recovery_resolution(
        _recovery_required(),
        current_state=state,
    )

    assert decision.status == RESOLUTION_BLOCKED
    assert decision.reason == REASON_ENTITY_STATE_UNAVAILABLE
    assert decision.can_mark_verified is False
    assert decision.can_redispatch is False
    assert decision.manual_review_required is True


def test_interrupted_dispatch_must_be_recovered_before_resolution() -> None:
    decision = evaluate_recovery_resolution(
        _dispatching(),
        current_state="on",
    )

    assert decision.status == RESOLUTION_BLOCKED
    assert decision.reason == REASON_INTERRUPTED_DISPATCH_NOT_RECOVERED
    assert decision.can_mark_verified is False
    assert decision.can_redispatch is False
    assert decision.manual_review_required is True


def test_prepared_lifecycle_has_nothing_to_recover() -> None:
    decision = evaluate_recovery_resolution(_prepared(), current_state="off")

    assert decision.status == RESOLUTION_NOT_APPLICABLE
    assert decision.reason == REASON_PREPARED_NOT_DISPATCHED
    assert decision.can_mark_verified is False
    assert decision.can_redispatch is False
    assert decision.manual_review_required is False


def test_verified_lifecycle_is_already_resolved() -> None:
    recovery = _recovery_required()
    verified = verify_desired_state(
        recovery,
        current_state="on",
        now=recovery.updated_at + 1,
    )

    decision = evaluate_recovery_resolution(verified, current_state="on")

    assert verified.state == STATE_VERIFIED
    assert decision.status == RESOLUTION_NOT_APPLICABLE
    assert decision.reason == REASON_ALREADY_VERIFIED
    assert decision.can_mark_verified is False
    assert decision.can_redispatch is False


@pytest.mark.parametrize("terminal", [STATE_FAILED, STATE_CANCELLED])
def test_other_terminal_lifecycles_are_not_recovery_candidates(terminal: str) -> None:
    prepared = _prepared()
    if terminal == STATE_CANCELLED:
        record = cancel_prepared(prepared, now=prepared.updated_at + 1)
    else:
        record = mark_failed(
            prepared,
            reason="cancelled by safety policy",
            now=prepared.updated_at + 1,
        )

    decision = evaluate_recovery_resolution(record, current_state="off")

    assert record.state == terminal
    assert decision.status == RESOLUTION_NOT_APPLICABLE
    assert decision.reason == REASON_TERMINAL_WITHOUT_VERIFICATION
    assert decision.can_mark_verified is False
    assert decision.can_redispatch is False
