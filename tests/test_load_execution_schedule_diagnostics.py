from datetime import datetime, timedelta, timezone

import pytest

from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import ExecutionLifecycleRecord
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import evaluate_execution_readiness
from custom_components.frakon_energy.load_execution_schedule import ExecutionSchedule
from custom_components.frakon_energy.load_execution_schedule_diagnostics import (
    ACTION_BLOCKED_RECOVERY,
    ACTION_NONE_EXPIRED,
    ACTION_NONE_LIFECYCLE,
    ACTION_PREPARE,
    ACTION_REVIEW_MISSED,
    ACTION_WAIT,
    TIMING_EXPIRED,
    TIMING_LIFECYCLE_EXISTS,
    TIMING_MISSED,
    TIMING_PREPARE_NOW,
    TIMING_WAITING,
    ScheduleDiagnosticError,
    evaluate_schedule_timing,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)


def _profile() -> LoadProfile:
    return LoadProfile("ev-home", "Enyaq", PROFILE_KIND_EV, 120, 11.0, entity_id="switch.enyaq_charging")


def _policy() -> LoadExecutionPolicy:
    return LoadExecutionPolicy("ev-home", mode=EXECUTION_MODE_APPROVAL_REQUIRED, max_power_kw=11.0, max_duration_minutes=120)


def _plan() -> LoadPlan:
    energy = 22.0
    return LoadPlan(
        load_id="ev-home",
        name="Enyaq",
        starts_at=START.isoformat(),
        ends_at=(START + timedelta(minutes=120)).isoformat(),
        duration_minutes=120,
        interval_count=8,
        power_kw=11.0,
        average_czk_kwh=2.0,
        minimum_czk_kwh=1.5,
        maximum_czk_kwh=2.5,
        estimated_energy_kwh=energy,
        estimated_cost_czk=44.0,
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
        approval_issued_at=int((START - timedelta(minutes=10)).timestamp()),
        approval_expires_at=int((START + timedelta(minutes=5)).timestamp()),
        created_at=int((START - timedelta(minutes=5)).timestamp()),
    ).validated()


def _snapshot() -> ExecutionActionSnapshot:
    attempt = _attempt()
    return ExecutionActionSnapshot.from_attempt_and_intent(
        attempt=attempt,
        intent=resolve_start_action_intent(_profile()),
        created_at=attempt.created_at,
    )


def _schedule() -> ExecutionSchedule:
    attempt = _attempt()
    snapshot = _snapshot()
    readiness = evaluate_execution_readiness(
        attempt=attempt,
        snapshot=snapshot,
        profile=_profile(),
        plan=_plan(),
        policy=_policy(),
        current_state="off",
        now=START - timedelta(minutes=2),
    )
    return ExecutionSchedule.from_approved_readiness(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=_plan(),
        readiness=readiness,
        created_at=int((START - timedelta(minutes=2)).timestamp()),
    )


def _lifecycle() -> ExecutionLifecycleRecord:
    attempt = _attempt()
    snapshot = _snapshot()
    readiness = evaluate_execution_readiness(
        attempt=attempt,
        snapshot=snapshot,
        profile=_profile(),
        plan=_plan(),
        policy=_policy(),
        current_state="off",
        now=START,
    )
    return ExecutionLifecycleRecord.prepared(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=_plan(),
        readiness=readiness,
        created_at=int(START.timestamp()),
    )


def test_waiting_before_start() -> None:
    result = evaluate_schedule_timing(
        _schedule(), lifecycle=None, now=START - timedelta(seconds=45), recovery_ready=True
    )

    assert result.status == TIMING_WAITING
    assert result.next_action == ACTION_WAIT
    assert result.seconds_until_start == 45
    assert result.scheduler_should_prepare is False
    assert result.final_readiness_required is True
    assert result.execution_performed is False


def test_exact_start_is_prepare_candidate() -> None:
    result = evaluate_schedule_timing(_schedule(), lifecycle=None, now=START, recovery_ready=True)

    assert result.status == TIMING_PREPARE_NOW
    assert result.next_action == ACTION_PREPARE
    assert result.seconds_until_start == 0
    assert result.seconds_until_prepare_deadline == 30
    assert result.scheduler_should_prepare is True


def test_exact_grace_deadline_is_still_prepare_candidate() -> None:
    result = evaluate_schedule_timing(
        _schedule(), lifecycle=None, now=START + timedelta(seconds=30), recovery_ready=True
    )

    assert result.status == TIMING_PREPARE_NOW
    assert result.scheduler_should_prepare is True


def test_after_grace_deadline_is_missed() -> None:
    result = evaluate_schedule_timing(
        _schedule(), lifecycle=None, now=START + timedelta(seconds=31), recovery_ready=True
    )

    assert result.status == TIMING_MISSED
    assert result.next_action == ACTION_REVIEW_MISSED
    assert result.scheduler_should_prepare is False


def test_after_plan_end_is_expired() -> None:
    result = evaluate_schedule_timing(
        _schedule(), lifecycle=None, now=START + timedelta(hours=2), recovery_ready=True
    )

    assert result.status == TIMING_EXPIRED
    assert result.next_action == ACTION_NONE_EXPIRED
    assert result.scheduler_should_prepare is False


def test_recovery_not_ready_blocks_scheduler_candidate() -> None:
    result = evaluate_schedule_timing(_schedule(), lifecycle=None, now=START, recovery_ready=False)

    assert result.status == TIMING_PREPARE_NOW
    assert result.next_action == ACTION_BLOCKED_RECOVERY
    assert result.scheduler_should_prepare is False
    assert result.recovery_ready is False


def test_existing_lifecycle_suppresses_further_prepare() -> None:
    lifecycle = _lifecycle()
    result = evaluate_schedule_timing(
        _schedule(), lifecycle=lifecycle, now=START + timedelta(seconds=5), recovery_ready=True
    )

    assert result.status == TIMING_LIFECYCLE_EXISTS
    assert result.next_action == ACTION_NONE_LIFECYCLE
    assert result.lifecycle_state == "prepared"
    assert result.scheduler_should_prepare is False


def test_naive_now_is_rejected() -> None:
    with pytest.raises(ScheduleDiagnosticError, match="timezone-aware"):
        evaluate_schedule_timing(
            _schedule(), lifecycle=None, now=datetime(2026, 8, 8, 1, 0), recovery_ready=True
        )
