from datetime import datetime, timedelta, timezone

from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import (
    begin_dispatch,
    ExecutionLifecycleRecord,
    require_recovery_after_restart,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import evaluate_execution_readiness
from custom_components.frakon_energy.load_execution_recovery_resolution import (
    REASON_STOP_OWNERSHIP_MISSING,
    RESOLUTION_BLOCKED,
    evaluate_recovery_resolution,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)


def _recovered() -> ExecutionLifecycleRecord:
    profile = LoadProfile(
        "ev-home",
        "Enyaq",
        PROFILE_KIND_EV,
        120,
        11.0,
        entity_id="switch.enyaq_charging",
    )
    policy = LoadExecutionPolicy(
        "ev-home",
        mode=EXECUTION_MODE_APPROVAL_REQUIRED,
        max_power_kw=11.0,
        max_duration_minutes=120,
    )
    plan = LoadPlan(
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
        estimated_energy_kwh=22.0,
        estimated_cost_czk=44.0,
    )
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
    prepared = ExecutionLifecycleRecord.prepared(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=plan,
        readiness=readiness,
        created_at=int(START.timestamp()),
    )
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)
    return require_recovery_after_restart(
        dispatching,
        now=dispatching.updated_at + 1,
    )


def test_desired_state_is_blocked_when_stop_ownership_is_missing() -> None:
    decision = evaluate_recovery_resolution(
        _recovered(),
        current_state="on",
        stop_ownership_ready=False,
    )

    assert decision.status == RESOLUTION_BLOCKED
    assert decision.reason == REASON_STOP_OWNERSHIP_MISSING
    assert decision.can_mark_verified is False
    assert decision.can_redispatch is False
    assert decision.manual_review_required is True
