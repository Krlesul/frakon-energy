"""Fail-closed execution policy evaluation for FRAKON Energy load plans.

This module deliberately does not execute Home Assistant services. It can only
classify a candidate plan as blocked or requiring explicit approval.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .energy_load_planner import LoadPlan
from .load_profiles import LoadProfile

OPTION_LOAD_EXECUTION_POLICIES = "load_execution_policies"

EXECUTION_MODE_DISABLED = "disabled"
EXECUTION_MODE_APPROVAL_REQUIRED = "approval_required"
EXECUTION_MODES = (EXECUTION_MODE_DISABLED, EXECUTION_MODE_APPROVAL_REQUIRED)

DECISION_BLOCKED = "blocked"
DECISION_APPROVAL_REQUIRED = "approval_required"

REASON_POLICY_DISABLED = "policy_disabled"
REASON_POLICY_PROFILE_MISMATCH = "policy_profile_mismatch"
REASON_PLAN_PROFILE_MISMATCH = "plan_profile_mismatch"
REASON_PROFILE_DISABLED = "profile_disabled"
REASON_ENTITY_BINDING_REQUIRED = "entity_binding_required"
REASON_ENTITY_UNAVAILABLE = "entity_unavailable"
REASON_POWER_LIMIT_EXCEEDED = "power_limit_exceeded"
REASON_DURATION_LIMIT_EXCEEDED = "duration_limit_exceeded"
REASON_PLAN_UNAVAILABLE = "plan_unavailable"


@dataclass(frozen=True, slots=True)
class LoadExecutionPolicy:
    """Safety envelope for a future execution request."""

    profile_id: str
    mode: str = EXECUTION_MODE_DISABLED
    max_power_kw: float | None = None
    max_duration_minutes: int | None = None
    require_entity_binding: bool = True
    require_entity_available: bool = True

    def validated(self) -> "LoadExecutionPolicy":
        if not self.profile_id.strip():
            raise ValueError("profile_id is required")
        if self.mode not in EXECUTION_MODES:
            raise ValueError(f"unsupported execution mode: {self.mode}")
        if self.max_power_kw is not None and self.max_power_kw <= 0:
            raise ValueError("max_power_kw must be positive")
        if self.max_duration_minutes is not None:
            if self.max_duration_minutes <= 0 or self.max_duration_minutes % 15 != 0:
                raise ValueError("max_duration_minutes must be a positive multiple of 15")
        if self.mode == EXECUTION_MODE_APPROVAL_REQUIRED:
            if self.max_power_kw is None:
                raise ValueError("approval_required mode requires max_power_kw")
            if self.max_duration_minutes is None:
                raise ValueError("approval_required mode requires max_duration_minutes")
        return self

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LoadExecutionPolicy":
        raw_power = value.get("max_power_kw")
        raw_duration = value.get("max_duration_minutes")
        return cls(
            profile_id=str(value.get("profile_id", "")),
            mode=str(value.get("mode", EXECUTION_MODE_DISABLED)),
            max_power_kw=float(raw_power) if raw_power is not None else None,
            max_duration_minutes=int(raw_duration) if raw_duration is not None else None,
            require_entity_binding=bool(value.get("require_entity_binding", True)),
            require_entity_available=bool(value.get("require_entity_available", True)),
        ).validated()


@dataclass(frozen=True, slots=True)
class ExecutionDecision:
    """Read-only decision produced before any future execution layer."""

    status: str
    profile_id: str
    entity_id: str | None
    reasons: tuple[str, ...]
    plan_starts_at: str
    plan_ends_at: str
    plan_power_kw: float
    plan_duration_minutes: int
    execution_performed: bool = False

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["reasons"] = list(self.reasons)
        return result


def policies_from_options(options: Mapping[str, Any]) -> tuple[LoadExecutionPolicy, ...]:
    """Load explicit execution policies from config-entry options."""
    raw = options.get(OPTION_LOAD_EXECUTION_POLICIES, [])
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("load_execution_policies must be a list")

    policies: list[LoadExecutionPolicy] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("each load execution policy must be an object")
        policy = LoadExecutionPolicy.from_dict(item)
        if policy.profile_id in seen:
            raise ValueError(f"duplicate execution policy profile_id: {policy.profile_id}")
        seen.add(policy.profile_id)
        policies.append(policy)
    return tuple(policies)


def policy_by_profile_id(options: Mapping[str, Any], profile_id: str) -> LoadExecutionPolicy:
    """Return one explicitly persisted policy or fail closed."""
    for policy in policies_from_options(options):
        if policy.profile_id == profile_id:
            return policy
    raise ValueError(f"load execution policy not found: {profile_id}")


def effective_policy_from_options(options: Mapping[str, Any], profile_id: str) -> LoadExecutionPolicy:
    """Return persisted policy or an implicit disabled policy."""
    try:
        return policy_by_profile_id(options, profile_id)
    except ValueError:
        return LoadExecutionPolicy(profile_id=profile_id, mode=EXECUTION_MODE_DISABLED)


def upsert_execution_policy(options: Mapping[str, Any], policy: LoadExecutionPolicy) -> dict[str, Any]:
    """Return config-entry options with one execution policy inserted or replaced."""
    policy.validated()
    policies = list(policies_from_options(options))
    for index, existing in enumerate(policies):
        if existing.profile_id == policy.profile_id:
            policies[index] = policy
            break
    else:
        policies.append(policy)
    updated = dict(options)
    updated[OPTION_LOAD_EXECUTION_POLICIES] = [item.as_dict() for item in policies]
    return updated


def delete_execution_policy(options: Mapping[str, Any], profile_id: str) -> dict[str, Any]:
    """Delete an explicit policy; the effective fallback becomes disabled."""
    policies = list(policies_from_options(options))
    if not any(item.profile_id == profile_id for item in policies):
        raise ValueError(f"load execution policy not found: {profile_id}")
    updated = dict(options)
    updated[OPTION_LOAD_EXECUTION_POLICIES] = [
        item.as_dict() for item in policies if item.profile_id != profile_id
    ]
    return updated


def evaluate_execution_policy(
    profile: LoadProfile,
    plan: LoadPlan,
    policy: LoadExecutionPolicy,
    *,
    entity_available: bool | None,
) -> ExecutionDecision:
    """Evaluate a plan without performing any action.

    A clean evaluation can only reach ``approval_required``. There is no
    automatic/approved result in this layer, so another explicit approval
    mechanism must exist before an executor can ever be introduced.
    """
    profile.validated()
    policy.validated()

    reasons: list[str] = []
    if policy.profile_id != profile.profile_id:
        reasons.append(REASON_POLICY_PROFILE_MISMATCH)
    if plan.load_id != profile.profile_id:
        reasons.append(REASON_PLAN_PROFILE_MISMATCH)
    if not profile.enabled:
        reasons.append(REASON_PROFILE_DISABLED)
    if policy.mode == EXECUTION_MODE_DISABLED:
        reasons.append(REASON_POLICY_DISABLED)
    if policy.require_entity_binding and not profile.entity_id:
        reasons.append(REASON_ENTITY_BINDING_REQUIRED)
    if policy.require_entity_available and profile.entity_id and entity_available is not True:
        reasons.append(REASON_ENTITY_UNAVAILABLE)
    if policy.max_power_kw is not None and plan.power_kw > policy.max_power_kw:
        reasons.append(REASON_POWER_LIMIT_EXCEEDED)
    if policy.max_duration_minutes is not None and plan.duration_minutes > policy.max_duration_minutes:
        reasons.append(REASON_DURATION_LIMIT_EXCEEDED)

    status = DECISION_BLOCKED if reasons else DECISION_APPROVAL_REQUIRED
    return ExecutionDecision(
        status=status,
        profile_id=profile.profile_id,
        entity_id=profile.entity_id,
        reasons=tuple(reasons),
        plan_starts_at=plan.starts_at,
        plan_ends_at=plan.ends_at,
        plan_power_kw=plan.power_kw,
        plan_duration_minutes=plan.duration_minutes,
        execution_performed=False,
    )
