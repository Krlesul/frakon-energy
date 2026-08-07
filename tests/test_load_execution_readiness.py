from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    EXECUTION_MODE_DISABLED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import (
    READINESS_ALREADY_SATISFIED,
    READINESS_BLOCKED,
    READINESS_READY,
    READINESS_WAITING,
    REASON_ALREADY_SATISFIED,
    REASON_ENTITY_ACTIVE_EARLY,
    REASON_PLAN_EXPIRED,
    REASON_POLICY_NOT_ELIGIBLE,
    REASON_READY,
    REASON_SCOPE_CHANGED,
    REASON_START_MISSED,
    REASON_WAITING_FOR_START,
    ExecutionReadinessError,
    evaluate_execution_readiness,
    load_plan_from_snapshot,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)


def _profile(*, entity_id: str = "switch.enyaq_charging", enabled: bool = True) -> LoadProfile:
    return LoadProfile(
        "ev-home",
        "Enyaq",
        PROFILE_KIND_EV,
        120,
        11.0,
        enabled=enabled,
        entity_id=entity_id,
    )


def _policy(*, mode: str = EXECUTION_MODE_APPROVAL_REQUIRED) -> LoadExecutionPolicy:
    if mode == EXECUTION_MODE_DISABLED:
        return LoadExecutionPolicy("ev-home", mode=mode)
    return LoadExecutionPolicy(
        "ev-home",
        mode=mode,
        max_power_kw=11.0,
        max_duration_minutes=120,
    )


def _plan(*, starts_at: datetime = START, average: float = 2.0) -> LoadPlan:
    duration = 120
    power = 11.0
    energy = power * duration / 60
    return LoadPlan(
        load_id="ev-home",
        name="Enyaq",
        starts_at=starts_at.isoformat(),
        ends_at=(starts_at + timedelta(minutes=duration)).isoformat(),
        duration_minutes=duration,
        interval_count=8,
        power_kw=power,
        average_czk_kwh=average,
        minimum_czk_kwh=1.5,
        maximum_czk_kwh=2.5,
        estimated_energy_kwh=energy,
        estimated_cost_czk=energy * average,
    )


def _attempt(
    *,
    profile: LoadProfile | None = None,
    plan: LoadPlan | None = None,
    policy: LoadExecutionPolicy | None = None,
) -> ExecutionAttempt:
    current_profile = profile or _profile()
    current_plan = plan or _plan()
    current_policy = policy or _policy()
    return ExecutionAttempt(
        attempt_id="attempt-1",
        entry_id="entry-1",
        profile_id=current_profile.profile_id,
        entity_id=current_profile.entity_id,
        approval_id="approval-1",
        approval_fingerprint="a" * 64,
        snapshot_digest=execution_snapshot_digest(current_profile, current_plan, current_policy),
        intent="execute_load_plan",
        approval_issued_at=int((START - timedelta(minutes=5)).timestamp()),
        approval_expires_at=int((START + timedelta(minutes=5)).timestamp()),
        created_at=int((START - timedelta(minutes=1)).timestamp()),
    ).validated()


def _snapshot(
    *,
    attempt: ExecutionAttempt | None = None,
    profile: LoadProfile | None = None,
) -> ExecutionActionSnapshot:
    current_attempt = attempt or _attempt()
    current_profile = profile or _profile()
    return ExecutionActionSnapshot.from_attempt_and_intent(
        attempt=current_attempt,
        intent=resolve_start_action_intent(current_profile),
        created_at=current_attempt.created_at,
    )


def _readiness(
    *,
    now: datetime,
    current_state: str | None = "off",
    profile: LoadProfile | None = None,
    plan: LoadPlan | None = None,
    policy: LoadExecutionPolicy | None = None,
    attempt: ExecutionAttempt | None = None,
    snapshot: ExecutionActionSnapshot | None = None,
):
    current_profile = profile or _profile()
    current_plan = plan or _plan()
    current_policy = policy or _policy()
    current_attempt = attempt or _attempt(
        profile=current_profile,
        plan=current_plan,
        policy=current_policy,
    )
    current_snapshot = snapshot or _snapshot(
        attempt=current_attempt,
        profile=current_profile,
    )
    return evaluate_execution_readiness(
        attempt=current_attempt,
        snapshot=current_snapshot,
        profile=current_profile,
        plan=current_plan,
        policy=current_policy,
        current_state=current_state,
        now=now,
    )


def test_exact_start_with_off_entity_is_ready() -> None:
    result = _readiness(now=START, current_state="off")

    assert result.status == READINESS_READY
    assert result.reason == REASON_READY
    assert result.seconds_until_start == 0
    assert result.approval_scope_matches is True
    assert result.policy_eligible is True
    assert result.attempt_matches is True
    assert result.profile_matches is True
    assert result.action_required is True
    assert result.execution_performed is False
    assert result.service_call_performed is False
    assert result.executor_available is False


def test_before_start_with_off_entity_waits() -> None:
    result = _readiness(now=START - timedelta(seconds=20), current_state="off")

    assert result.status == READINESS_WAITING
    assert result.reason == REASON_WAITING_FOR_START
    assert result.seconds_until_start == 20
    assert result.action_required is False


def test_entity_already_on_before_start_is_blocked() -> None:
    result = _readiness(now=START - timedelta(seconds=20), current_state="on")

    assert result.status == READINESS_BLOCKED
    assert result.reason == REASON_ENTITY_ACTIVE_EARLY
    assert result.action_required is False


def test_entity_on_at_start_is_already_satisfied() -> None:
    result = _readiness(now=START + timedelta(seconds=5), current_state="on")

    assert result.status == READINESS_ALREADY_SATISFIED
    assert result.reason == REASON_ALREADY_SATISFIED
    assert result.action_required is False


def test_start_grace_expiry_is_blocked() -> None:
    result = _readiness(now=START + timedelta(seconds=31), current_state="off")

    assert result.status == READINESS_BLOCKED
    assert result.reason == REASON_START_MISSED
    assert result.action_required is False


def test_plan_end_is_expired() -> None:
    result = _readiness(now=START + timedelta(hours=2), current_state="off")

    assert result.status == READINESS_BLOCKED
    assert result.reason == REASON_PLAN_EXPIRED


def test_tampered_plan_price_changes_approval_scope_and_blocks() -> None:
    approved_plan = _plan()
    attempt = _attempt(plan=approved_plan)
    snapshot = _snapshot(attempt=attempt)
    tampered_plan = _plan(average=2.1)

    result = _readiness(
        now=START,
        plan=tampered_plan,
        attempt=attempt,
        snapshot=snapshot,
    )

    assert result.status == READINESS_BLOCKED
    assert result.reason == REASON_SCOPE_CHANGED
    assert result.approval_scope_matches is False
    assert result.action_required is False


def test_policy_change_after_approval_changes_scope_and_blocks() -> None:
    approved_policy = _policy()
    attempt = _attempt(policy=approved_policy)
    snapshot = _snapshot(attempt=attempt)
    changed_policy = LoadExecutionPolicy(
        "ev-home",
        mode=EXECUTION_MODE_APPROVAL_REQUIRED,
        max_power_kw=10.0,
        max_duration_minutes=120,
    )

    result = _readiness(
        now=START,
        policy=changed_policy,
        attempt=attempt,
        snapshot=snapshot,
    )

    assert result.status == READINESS_BLOCKED
    assert result.reason == REASON_SCOPE_CHANGED
    assert result.approval_scope_matches is False


def test_defensive_disabled_policy_snapshot_is_not_eligible() -> None:
    disabled = _policy(mode=EXECUTION_MODE_DISABLED)
    attempt = _attempt(policy=disabled)
    snapshot = _snapshot(attempt=attempt)

    result = _readiness(
        now=START,
        policy=disabled,
        attempt=attempt,
        snapshot=snapshot,
    )

    assert result.status == READINESS_BLOCKED
    assert result.reason == REASON_POLICY_NOT_ELIGIBLE
    assert result.approval_scope_matches is True
    assert result.policy_eligible is False


def test_changed_entity_binding_is_blocked_before_timing() -> None:
    approved_profile = _profile()
    attempt = _attempt(profile=approved_profile)
    snapshot = _snapshot(attempt=attempt, profile=approved_profile)
    changed_profile = _profile(entity_id="switch.other")

    result = _readiness(
        now=START,
        profile=changed_profile,
        attempt=attempt,
        snapshot=snapshot,
    )

    assert result.status == READINESS_BLOCKED
    assert result.reason == "profile_or_action_mapping_changed"
    assert result.profile_matches is False


def test_unavailable_entity_is_blocked() -> None:
    result = _readiness(now=START, current_state="unavailable")

    assert result.status == READINESS_BLOCKED
    assert result.action_required is False
    assert result.executor_available is False


def test_load_plan_snapshot_parser_accepts_exact_plan() -> None:
    raw = _plan().as_dict()
    parsed = load_plan_from_snapshot(_profile(), raw)

    assert parsed == _plan()


def test_load_plan_snapshot_parser_rejects_profile_mismatch() -> None:
    raw = _plan().as_dict()
    raw["load_id"] = "other"

    with pytest.raises(ExecutionReadinessError, match="load_id does not match"):
        load_plan_from_snapshot(_profile(), raw)


def test_load_plan_snapshot_parser_rejects_inconsistent_cost() -> None:
    raw = _plan().as_dict()
    raw["estimated_cost_czk"] = 999.0

    with pytest.raises(ExecutionReadinessError, match="estimated_cost_czk is inconsistent"):
        load_plan_from_snapshot(_profile(), raw)


def test_naive_now_is_rejected() -> None:
    with pytest.raises(ExecutionReadinessError, match="timezone-aware"):
        _readiness(now=datetime(2026, 8, 8, 1, 0), current_state="off")


def test_start_grace_larger_than_server_limit_is_rejected() -> None:
    with pytest.raises(ExecutionReadinessError, match="between 0 and 60"):
        evaluate_execution_readiness(
            attempt=_attempt(),
            snapshot=_snapshot(),
            profile=_profile(),
            plan=_plan(),
            policy=_policy(),
            current_state="off",
            now=START,
            start_grace_seconds=61,
        )
