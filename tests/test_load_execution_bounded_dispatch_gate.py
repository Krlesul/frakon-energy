from dataclasses import replace
from datetime import datetime, timedelta, timezone

from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_bounded_dispatch_gate import (
    BOUNDED_GATE_ALREADY_SATISFIED,
    BOUNDED_GATE_BLOCKED,
    BOUNDED_GATE_READY,
    REASON_DISPATCH_GATE_MISMATCH,
    REASON_READY,
    REASON_STOP_LEASE_MISMATCH,
    REASON_STOP_LEASE_REQUIRED,
    evaluate_bounded_dispatch_gate,
)
from custom_components.frakon_energy.load_execution_dispatch_gate import (
    DISPATCH_GATE_BLOCKED,
    evaluate_dispatch_gate,
)
from custom_components.frakon_energy.load_execution_lifecycle import ExecutionLifecycleRecord
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import evaluate_execution_readiness
from custom_components.frakon_energy.load_execution_stop_lease import ExecutionStopLease
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


def _bundle(*, current_state: str = "off", now: datetime = START):
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
    prepared_readiness = evaluate_execution_readiness(
        attempt=attempt,
        snapshot=snapshot,
        profile=profile,
        plan=plan,
        policy=policy,
        current_state="off",
        now=START,
    )
    lifecycle = ExecutionLifecycleRecord.prepared(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=plan,
        readiness=prepared_readiness,
        created_at=int(START.timestamp()),
    )
    current_readiness = evaluate_execution_readiness(
        attempt=attempt,
        snapshot=snapshot,
        profile=profile,
        plan=plan,
        policy=policy,
        current_state=current_state,
        now=now,
    )
    dispatch_gate = evaluate_dispatch_gate(
        lifecycle=lifecycle,
        attempt=attempt,
        snapshot=snapshot,
        readiness=current_readiness,
    )
    lease = ExecutionStopLease.from_prepared_lifecycle(
        lifecycle,
        created_at=lifecycle.created_at,
    )
    return lifecycle, dispatch_gate, lease


def test_matching_armed_stop_lease_unlocks_bounded_start() -> None:
    lifecycle, dispatch_gate, lease = _bundle()

    decision = evaluate_bounded_dispatch_gate(
        lifecycle=lifecycle,
        dispatch_gate=dispatch_gate,
        stop_lease=lease,
    )

    assert decision.status == BOUNDED_GATE_READY
    assert decision.reason == REASON_READY
    assert decision.dispatch_gate_matches is True
    assert decision.stop_lease_matches is True
    assert decision.can_start is True
    assert decision.can_redispatch is False
    assert decision.stop_service_domain == "switch"
    assert decision.stop_service_name == "turn_off"
    assert decision.stop_at == _plan().ends_at
    assert decision.state_transition_performed is False
    assert decision.service_call_performed is False
    assert decision.execution_performed is False
    assert decision.executor_available is False


def test_missing_stop_lease_blocks_bounded_start() -> None:
    lifecycle, dispatch_gate, _ = _bundle()

    decision = evaluate_bounded_dispatch_gate(
        lifecycle=lifecycle,
        dispatch_gate=dispatch_gate,
        stop_lease=None,
    )

    assert decision.status == BOUNDED_GATE_BLOCKED
    assert decision.reason == REASON_STOP_LEASE_REQUIRED
    assert decision.stop_lease_matches is False
    assert decision.can_start is False


def test_tampered_stop_lease_blocks_bounded_start() -> None:
    lifecycle, dispatch_gate, lease = _bundle()
    tampered = replace(lease, entity_id="switch.other")

    decision = evaluate_bounded_dispatch_gate(
        lifecycle=lifecycle,
        dispatch_gate=dispatch_gate,
        stop_lease=tampered,
    )

    assert decision.status == BOUNDED_GATE_BLOCKED
    assert decision.reason == REASON_STOP_LEASE_MISMATCH
    assert decision.stop_lease_matches is False
    assert decision.can_start is False


def test_dispatch_gate_binding_drift_blocks_even_with_matching_lease() -> None:
    lifecycle, dispatch_gate, lease = _bundle()
    stale_gate = replace(dispatch_gate, attempt_id="attempt-other")

    decision = evaluate_bounded_dispatch_gate(
        lifecycle=lifecycle,
        dispatch_gate=stale_gate,
        stop_lease=lease,
    )

    assert decision.status == BOUNDED_GATE_BLOCKED
    assert decision.reason == REASON_DISPATCH_GATE_MISMATCH
    assert decision.dispatch_gate_matches is False
    assert decision.can_start is False


def test_already_satisfied_never_starts_even_if_lease_exists() -> None:
    lifecycle, dispatch_gate, lease = _bundle(current_state="on")

    decision = evaluate_bounded_dispatch_gate(
        lifecycle=lifecycle,
        dispatch_gate=dispatch_gate,
        stop_lease=lease,
    )

    assert decision.status == BOUNDED_GATE_ALREADY_SATISFIED
    assert decision.can_start is False
    assert decision.can_redispatch is False


def test_dispatch_gate_block_reason_is_propagated() -> None:
    lifecycle, dispatch_gate, lease = _bundle(now=START + timedelta(seconds=31))
    assert dispatch_gate.status == DISPATCH_GATE_BLOCKED

    decision = evaluate_bounded_dispatch_gate(
        lifecycle=lifecycle,
        dispatch_gate=dispatch_gate,
        stop_lease=lease,
    )

    assert decision.status == BOUNDED_GATE_BLOCKED
    assert decision.reason == dispatch_gate.reason
    assert decision.can_start is False
