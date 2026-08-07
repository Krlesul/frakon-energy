from dataclasses import replace
from datetime import datetime, timedelta, timezone

from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_dispatch_gate import (
    DISPATCH_GATE_ALREADY_SATISFIED,
    DISPATCH_GATE_BLOCKED,
    DISPATCH_GATE_READY,
    REASON_ALREADY_SATISFIED,
    REASON_LIFECYCLE_BINDING_CHANGED,
    REASON_LIFECYCLE_NOT_PREPARED,
    REASON_READINESS_EVIDENCE_INVALID,
    REASON_READY,
    evaluate_dispatch_gate,
)
from custom_components.frakon_energy.load_execution_lifecycle import (
    ExecutionLifecycleRecord,
    begin_dispatch,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import (
    READINESS_ALREADY_SATISFIED,
    READINESS_BLOCKED,
    READINESS_READY,
    REASON_START_MISSED,
    evaluate_execution_readiness,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)


def _profile(*, entity_id: str = "switch.enyaq_charging") -> LoadProfile:
    return LoadProfile(
        "ev-home",
        "Enyaq",
        PROFILE_KIND_EV,
        120,
        11.0,
        entity_id=entity_id,
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


def _attempt(*, entity_id: str = "switch.enyaq_charging") -> ExecutionAttempt:
    return ExecutionAttempt(
        attempt_id="attempt-1",
        entry_id="entry-1",
        profile_id="ev-home",
        entity_id=entity_id,
        approval_id="approval-1",
        approval_fingerprint="a" * 64,
        snapshot_digest=execution_snapshot_digest(_profile(), _plan(), _policy()),
        intent="execute_load_plan",
        approval_issued_at=int((START - timedelta(minutes=5)).timestamp()),
        approval_expires_at=int((START + timedelta(minutes=5)).timestamp()),
        created_at=int((START - timedelta(minutes=1)).timestamp()),
    ).validated()


def _snapshot(attempt: ExecutionAttempt | None = None) -> ExecutionActionSnapshot:
    current = attempt or _attempt()
    return ExecutionActionSnapshot.from_attempt_and_intent(
        attempt=current,
        intent=resolve_start_action_intent(_profile(entity_id=current.entity_id or "switch.enyaq_charging")),
        created_at=current.created_at,
    )


def _bundle(*, current_state: str = "off", now: datetime = START):
    profile = _profile()
    policy = _policy()
    plan = _plan()
    attempt = _attempt()
    snapshot = _snapshot(attempt)
    readiness = evaluate_execution_readiness(
        attempt=attempt,
        snapshot=snapshot,
        profile=profile,
        plan=plan,
        policy=policy,
        current_state=current_state,
        now=now,
    )
    lifecycle = ExecutionLifecycleRecord.prepared(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=plan,
        readiness=evaluate_execution_readiness(
            attempt=attempt,
            snapshot=snapshot,
            profile=profile,
            plan=plan,
            policy=policy,
            current_state="off",
            now=START,
        ),
        created_at=int(START.timestamp()),
    )
    return lifecycle, attempt, snapshot, readiness


def test_ready_prepared_lifecycle_is_dispatchable_only_once() -> None:
    lifecycle, attempt, snapshot, readiness = _bundle()

    gate = evaluate_dispatch_gate(
        lifecycle=lifecycle,
        attempt=attempt,
        snapshot=snapshot,
        readiness=readiness,
    )

    assert readiness.status == READINESS_READY
    assert gate.status == DISPATCH_GATE_READY
    assert gate.reason == REASON_READY
    assert gate.lifecycle_binding_matches is True
    assert gate.can_dispatch is True
    assert gate.can_redispatch is False
    assert gate.state_transition_performed is False
    assert gate.service_call_performed is False
    assert gate.execution_performed is False
    assert gate.executor_available is False


def test_already_satisfied_is_explicit_no_dispatch() -> None:
    lifecycle, attempt, snapshot, readiness = _bundle(current_state="on")

    gate = evaluate_dispatch_gate(
        lifecycle=lifecycle,
        attempt=attempt,
        snapshot=snapshot,
        readiness=readiness,
    )

    assert readiness.status == READINESS_ALREADY_SATISFIED
    assert gate.status == DISPATCH_GATE_ALREADY_SATISFIED
    assert gate.reason == REASON_ALREADY_SATISFIED
    assert gate.can_dispatch is False
    assert gate.can_redispatch is False


def test_missed_start_is_blocked_with_readiness_reason() -> None:
    lifecycle, attempt, snapshot, readiness = _bundle(
        now=START + timedelta(seconds=31),
    )

    gate = evaluate_dispatch_gate(
        lifecycle=lifecycle,
        attempt=attempt,
        snapshot=snapshot,
        readiness=readiness,
    )

    assert readiness.status == READINESS_BLOCKED
    assert readiness.reason == REASON_START_MISSED
    assert gate.status == DISPATCH_GATE_BLOCKED
    assert gate.reason == REASON_START_MISSED
    assert gate.can_dispatch is False


def test_non_prepared_lifecycle_is_blocked_even_with_ready_evidence() -> None:
    lifecycle, attempt, snapshot, readiness = _bundle()
    dispatching = begin_dispatch(lifecycle, now=lifecycle.updated_at + 1)

    gate = evaluate_dispatch_gate(
        lifecycle=dispatching,
        attempt=attempt,
        snapshot=snapshot,
        readiness=readiness,
    )

    assert gate.status == DISPATCH_GATE_BLOCKED
    assert gate.reason == REASON_LIFECYCLE_NOT_PREPARED
    assert gate.can_dispatch is False


def test_attempt_binding_drift_is_blocked() -> None:
    lifecycle, _, snapshot, readiness = _bundle()
    changed_attempt = _attempt(entity_id="switch.other")

    gate = evaluate_dispatch_gate(
        lifecycle=lifecycle,
        attempt=changed_attempt,
        snapshot=snapshot,
        readiness=readiness,
    )

    assert gate.status == DISPATCH_GATE_BLOCKED
    assert gate.reason == REASON_LIFECYCLE_BINDING_CHANGED
    assert gate.lifecycle_binding_matches is False
    assert gate.can_dispatch is False


def test_impossible_readiness_execution_evidence_is_blocked() -> None:
    lifecycle, attempt, snapshot, readiness = _bundle()
    impossible = replace(readiness, service_call_performed=True)

    gate = evaluate_dispatch_gate(
        lifecycle=lifecycle,
        attempt=attempt,
        snapshot=snapshot,
        readiness=impossible,
    )

    assert gate.status == DISPATCH_GATE_BLOCKED
    assert gate.reason == REASON_READINESS_EVIDENCE_INVALID
    assert gate.can_dispatch is False
