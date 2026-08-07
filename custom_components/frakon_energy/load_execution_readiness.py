"""Final read-only readiness gate before any future FRAKON Energy executor.

The gate combines a persistent consumed attempt, immutable action snapshot,
current profile/policy/entity state and an exact plan snapshot. It never calls a
Home Assistant service and cannot authorize arbitrary actions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hmac
import math
from typing import Any, Mapping

from .energy_load_planner import LoadPlan
from .load_execution_action_snapshot import (
    ExecutionActionSnapshot,
    revalidate_action_snapshot,
)
from .load_execution_approval import execution_snapshot_digest
from .load_execution_attempt import ExecutionAttempt
from .load_execution_policy import (
    DECISION_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
    evaluate_execution_policy,
)
from .load_profiles import LoadProfile

READINESS_READY = "ready"
READINESS_WAITING = "waiting"
READINESS_ALREADY_SATISFIED = "already_satisfied"
READINESS_BLOCKED = "blocked"

REASON_READY = "approved_start_window_open"
REASON_WAITING_FOR_START = "plan_start_in_future"
REASON_ALREADY_SATISFIED = "entity_already_in_desired_state_at_start"
REASON_ENTITY_ACTIVE_EARLY = "entity_active_before_plan_start"
REASON_START_MISSED = "approved_start_window_missed"
REASON_PLAN_EXPIRED = "approved_plan_window_expired"
REASON_SCOPE_CHANGED = "approval_scope_changed"
REASON_POLICY_NOT_ELIGIBLE = "policy_not_eligible"
REASON_PLAN_INVALID = "plan_invalid"

DEFAULT_START_GRACE_SECONDS = 30
MAX_START_GRACE_SECONDS = 60


class ExecutionReadinessError(ValueError):
    """Raised when the supplied plan snapshot is structurally invalid."""


def _aware_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ExecutionReadinessError(f"plan {field} must be an ISO-8601 datetime")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as err:
        raise ExecutionReadinessError(f"plan {field} must be an ISO-8601 datetime") from err
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExecutionReadinessError(f"plan {field} must include a timezone offset")
    return parsed


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ExecutionReadinessError(f"plan {field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as err:
        raise ExecutionReadinessError(f"plan {field} must be numeric") from err
    if not math.isfinite(number):
        raise ExecutionReadinessError(f"plan {field} must be finite")
    return number


def load_plan_from_snapshot(profile: LoadProfile, value: Mapping[str, Any]) -> LoadPlan:
    """Parse and validate an exact immutable plan candidate without trusting the client."""
    profile.validated()
    if not isinstance(value, Mapping):
        raise ExecutionReadinessError("plan must be an object")
    starts = _aware_datetime(value.get("starts_at"), "starts_at")
    ends = _aware_datetime(value.get("ends_at"), "ends_at")
    if ends <= starts:
        raise ExecutionReadinessError("plan ends_at must be after starts_at")

    load_id = value.get("load_id")
    name = value.get("name")
    if load_id != profile.profile_id:
        raise ExecutionReadinessError("plan load_id does not match profile")
    if name != profile.name:
        raise ExecutionReadinessError("plan name does not match profile")

    duration = value.get("duration_minutes")
    interval_count = value.get("interval_count")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0 or duration % 15 != 0:
        raise ExecutionReadinessError("plan duration_minutes must be a positive multiple of 15")
    if not isinstance(interval_count, int) or isinstance(interval_count, bool) or interval_count != duration // 15:
        raise ExecutionReadinessError("plan interval_count does not match duration_minutes")
    if int((ends - starts).total_seconds()) != duration * 60:
        raise ExecutionReadinessError("plan time window does not match duration_minutes")

    power = _finite_number(value.get("power_kw"), "power_kw")
    average = _finite_number(value.get("average_czk_kwh"), "average_czk_kwh")
    minimum = _finite_number(value.get("minimum_czk_kwh"), "minimum_czk_kwh")
    maximum = _finite_number(value.get("maximum_czk_kwh"), "maximum_czk_kwh")
    energy = _finite_number(value.get("estimated_energy_kwh"), "estimated_energy_kwh")
    cost = _finite_number(value.get("estimated_cost_czk"), "estimated_cost_czk")
    if power <= 0:
        raise ExecutionReadinessError("plan power_kw must be positive")
    if minimum > average or average > maximum:
        raise ExecutionReadinessError("plan average price must be within minimum and maximum")
    expected_energy = power * duration / 60
    if not math.isclose(energy, expected_energy, rel_tol=1e-9, abs_tol=1e-9):
        raise ExecutionReadinessError("plan estimated_energy_kwh is inconsistent")
    if not math.isclose(cost, energy * average, rel_tol=1e-9, abs_tol=1e-9):
        raise ExecutionReadinessError("plan estimated_cost_czk is inconsistent")

    return LoadPlan(
        load_id=profile.profile_id,
        name=profile.name,
        starts_at=starts.isoformat(),
        ends_at=ends.isoformat(),
        duration_minutes=duration,
        interval_count=interval_count,
        power_kw=power,
        average_czk_kwh=average,
        minimum_czk_kwh=minimum,
        maximum_czk_kwh=maximum,
        estimated_energy_kwh=energy,
        estimated_cost_czk=cost,
    )


@dataclass(frozen=True, slots=True)
class ExecutionReadinessDecision:
    """Fail-closed decision immediately preceding a future executor layer."""

    status: str
    reason: str
    attempt_id: str
    action_snapshot_id: str
    profile_id: str
    entity_id: str
    current_state: str | None
    desired_state: str
    plan_starts_at: str
    plan_ends_at: str
    seconds_until_start: int
    start_grace_seconds: int
    approval_scope_matches: bool
    policy_eligible: bool
    attempt_matches: bool
    profile_matches: bool
    action_required: bool
    execution_performed: bool = False
    service_call_performed: bool = False
    executor_available: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalized_state(value: str | None) -> str | None:
    return value.strip().lower() if isinstance(value, str) else None


def _entity_available(current_state: str | None) -> bool:
    normalized = _normalized_state(current_state)
    return normalized not in (None, "unknown", "unavailable")


def _decision(
    *,
    status: str,
    reason: str,
    attempt: ExecutionAttempt,
    snapshot: ExecutionActionSnapshot,
    current_state: str | None,
    plan: LoadPlan,
    now: datetime,
    start_grace_seconds: int,
    approval_scope_matches: bool,
    policy_eligible: bool,
    attempt_matches: bool,
    profile_matches: bool,
    action_required: bool,
) -> ExecutionReadinessDecision:
    starts = datetime.fromisoformat(plan.starts_at)
    return ExecutionReadinessDecision(
        status=status,
        reason=reason,
        attempt_id=attempt.attempt_id,
        action_snapshot_id=snapshot.snapshot_id,
        profile_id=attempt.profile_id,
        entity_id=snapshot.entity_id,
        current_state=_normalized_state(current_state),
        desired_state=snapshot.desired_state,
        plan_starts_at=plan.starts_at,
        plan_ends_at=plan.ends_at,
        seconds_until_start=int(starts.timestamp()) - int(now.timestamp()),
        start_grace_seconds=start_grace_seconds,
        approval_scope_matches=approval_scope_matches,
        policy_eligible=policy_eligible,
        attempt_matches=attempt_matches,
        profile_matches=profile_matches,
        action_required=action_required,
    )


def evaluate_execution_readiness(
    *,
    attempt: ExecutionAttempt,
    snapshot: ExecutionActionSnapshot,
    profile: LoadProfile,
    plan: LoadPlan,
    policy: LoadExecutionPolicy,
    current_state: str | None,
    now: datetime,
    start_grace_seconds: int = DEFAULT_START_GRACE_SECONDS,
) -> ExecutionReadinessDecision:
    """Evaluate exact execution readiness without executing any Home Assistant action."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ExecutionReadinessError("now must be timezone-aware")
    if start_grace_seconds < 0 or start_grace_seconds > MAX_START_GRACE_SECONDS:
        raise ExecutionReadinessError(
            f"start_grace_seconds must be between 0 and {MAX_START_GRACE_SECONDS}"
        )

    attempt.validated()
    snapshot.validated()
    profile.validated()
    policy.validated()
    starts = _aware_datetime(plan.starts_at, "starts_at")
    ends = _aware_datetime(plan.ends_at, "ends_at")

    action_revalidation = revalidate_action_snapshot(
        snapshot,
        attempt=attempt,
        profile=profile,
        current_state=current_state,
    )
    if not action_revalidation.attempt_matches or not action_revalidation.profile_matches:
        return _decision(
            status=READINESS_BLOCKED,
            reason=action_revalidation.reason,
            attempt=attempt,
            snapshot=snapshot,
            current_state=current_state,
            plan=plan,
            now=now,
            start_grace_seconds=start_grace_seconds,
            approval_scope_matches=False,
            policy_eligible=False,
            attempt_matches=action_revalidation.attempt_matches,
            profile_matches=action_revalidation.profile_matches,
            action_required=False,
        )

    current_digest = execution_snapshot_digest(profile, plan, policy)
    scope_matches = hmac.compare_digest(attempt.snapshot_digest, current_digest)
    if not scope_matches:
        return _decision(
            status=READINESS_BLOCKED,
            reason=REASON_SCOPE_CHANGED,
            attempt=attempt,
            snapshot=snapshot,
            current_state=current_state,
            plan=plan,
            now=now,
            start_grace_seconds=start_grace_seconds,
            approval_scope_matches=False,
            policy_eligible=False,
            attempt_matches=True,
            profile_matches=True,
            action_required=False,
        )

    policy_decision = evaluate_execution_policy(
        profile,
        plan,
        policy,
        entity_available=_entity_available(current_state),
    )
    policy_eligible = (
        policy_decision.status == DECISION_APPROVAL_REQUIRED
        and not policy_decision.reasons
    )
    if not policy_eligible:
        return _decision(
            status=READINESS_BLOCKED,
            reason=REASON_POLICY_NOT_ELIGIBLE,
            attempt=attempt,
            snapshot=snapshot,
            current_state=current_state,
            plan=plan,
            now=now,
            start_grace_seconds=start_grace_seconds,
            approval_scope_matches=True,
            policy_eligible=False,
            attempt_matches=True,
            profile_matches=True,
            action_required=False,
        )

    normalized_state = _normalized_state(current_state)
    if action_revalidation.status == "blocked":
        return _decision(
            status=READINESS_BLOCKED,
            reason=action_revalidation.reason,
            attempt=attempt,
            snapshot=snapshot,
            current_state=current_state,
            plan=plan,
            now=now,
            start_grace_seconds=start_grace_seconds,
            approval_scope_matches=True,
            policy_eligible=True,
            attempt_matches=True,
            profile_matches=True,
            action_required=False,
        )

    if now >= ends:
        return _decision(
            status=READINESS_BLOCKED,
            reason=REASON_PLAN_EXPIRED,
            attempt=attempt,
            snapshot=snapshot,
            current_state=current_state,
            plan=plan,
            now=now,
            start_grace_seconds=start_grace_seconds,
            approval_scope_matches=True,
            policy_eligible=True,
            attempt_matches=True,
            profile_matches=True,
            action_required=False,
        )

    if now < starts:
        if normalized_state == snapshot.desired_state:
            return _decision(
                status=READINESS_BLOCKED,
                reason=REASON_ENTITY_ACTIVE_EARLY,
                attempt=attempt,
                snapshot=snapshot,
                current_state=current_state,
                plan=plan,
                now=now,
                start_grace_seconds=start_grace_seconds,
                approval_scope_matches=True,
                policy_eligible=True,
                attempt_matches=True,
                profile_matches=True,
                action_required=False,
            )
        return _decision(
            status=READINESS_WAITING,
            reason=REASON_WAITING_FOR_START,
            attempt=attempt,
            snapshot=snapshot,
            current_state=current_state,
            plan=plan,
            now=now,
            start_grace_seconds=start_grace_seconds,
            approval_scope_matches=True,
            policy_eligible=True,
            attempt_matches=True,
            profile_matches=True,
            action_required=False,
        )

    grace_deadline = starts.timestamp() + start_grace_seconds
    if now.timestamp() > grace_deadline:
        return _decision(
            status=READINESS_BLOCKED,
            reason=REASON_START_MISSED,
            attempt=attempt,
            snapshot=snapshot,
            current_state=current_state,
            plan=plan,
            now=now,
            start_grace_seconds=start_grace_seconds,
            approval_scope_matches=True,
            policy_eligible=True,
            attempt_matches=True,
            profile_matches=True,
            action_required=False,
        )

    if normalized_state == snapshot.desired_state:
        return _decision(
            status=READINESS_ALREADY_SATISFIED,
            reason=REASON_ALREADY_SATISFIED,
            attempt=attempt,
            snapshot=snapshot,
            current_state=current_state,
            plan=plan,
            now=now,
            start_grace_seconds=start_grace_seconds,
            approval_scope_matches=True,
            policy_eligible=True,
            attempt_matches=True,
            profile_matches=True,
            action_required=False,
        )

    return _decision(
        status=READINESS_READY,
        reason=REASON_READY,
        attempt=attempt,
        snapshot=snapshot,
        current_state=current_state,
        plan=plan,
        now=now,
        start_grace_seconds=start_grace_seconds,
        approval_scope_matches=True,
        policy_eligible=True,
        attempt_matches=True,
        profile_matches=True,
        action_required=True,
    )
