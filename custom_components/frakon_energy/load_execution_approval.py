"""Short-lived one-time approvals for future FRAKON Energy load execution.

Approvals are intentionally runtime-only and fail closed on restart. This module
contains no Home Assistant service calls and cannot execute a device action.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
from typing import Any, Mapping

APPROVAL_STATUS_PENDING = "pending"
APPROVAL_STATUS_APPROVED = "approved"
APPROVAL_STATUS_USED = "used"
APPROVAL_STATUS_REVOKED = "revoked"
APPROVAL_STATUS_EXPIRED = "expired"

APPROVAL_TTL_DEFAULT_SECONDS = 300
APPROVAL_TTL_MIN_SECONDS = 30
APPROVAL_TTL_MAX_SECONDS = 900

VALIDATION_NOT_APPROVED = "not_approved"
VALIDATION_EXPIRED = "expired"
VALIDATION_SCOPE_CHANGED = "scope_changed"
VALIDATION_PLAN_STARTED = "plan_started"
VALIDATION_ALREADY_USED = "already_used"
VALIDATION_REVOKED = "revoked"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_aware(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"{field} must be an ISO-8601 datetime") from err
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return parsed


@dataclass(frozen=True, slots=True)
class ApprovalScope:
    """Immutable execution scope that an approval authorizes exactly once."""

    entry_id: str
    profile_id: str
    entity_id: str
    plan_starts_at: str
    plan_ends_at: str
    plan_power_kw: float
    plan_duration_minutes: int
    plan_average_czk_kwh: float
    plan_estimated_cost_czk: float
    policy_mode: str
    policy_max_power_kw: float
    policy_max_duration_minutes: int
    policy_require_entity_binding: bool
    policy_require_entity_available: bool

    def validated(self) -> "ApprovalScope":
        if not self.entry_id.strip():
            raise ValueError("entry_id is required")
        if not self.profile_id.strip():
            raise ValueError("profile_id is required")
        if not self.entity_id.strip():
            raise ValueError("entity_id is required for approval scope")
        starts = _parse_aware(self.plan_starts_at, "plan_starts_at")
        ends = _parse_aware(self.plan_ends_at, "plan_ends_at")
        if starts >= ends:
            raise ValueError("plan_starts_at must be before plan_ends_at")
        if self.plan_power_kw <= 0:
            raise ValueError("plan_power_kw must be positive")
        if self.plan_duration_minutes <= 0 or self.plan_duration_minutes % 15 != 0:
            raise ValueError("plan_duration_minutes must be a positive multiple of 15")
        if self.policy_mode != "approval_required":
            raise ValueError("approval scope requires approval_required policy")
        if self.policy_max_power_kw <= 0:
            raise ValueError("policy_max_power_kw must be positive")
        if self.policy_max_duration_minutes <= 0 or self.policy_max_duration_minutes % 15 != 0:
            raise ValueError("policy_max_duration_minutes must be a positive multiple of 15")
        return self

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def fingerprint(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class LoadExecutionApproval:
    """Runtime-only approval request bound to an immutable execution scope."""

    approval_id: str
    scope: ApprovalScope
    scope_hash: str
    created_at: str
    expires_at: str
    status: str = APPROVAL_STATUS_PENDING
    approved_at: str | None = None
    approved_by: str | None = None
    revoked_at: str | None = None
    used_at: str | None = None

    def effective_status(self, now: datetime | None = None) -> str:
        current = now or _utc_now()
        if self.status in {APPROVAL_STATUS_PENDING, APPROVAL_STATUS_APPROVED}:
            if current >= _parse_aware(self.expires_at, "expires_at"):
                return APPROVAL_STATUS_EXPIRED
        return self.status

    def as_dict(self, *, now: datetime | None = None) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "scope": self.scope.as_dict(),
            "scope_hash": self.scope_hash,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "status": self.effective_status(now),
            "stored_status": self.status,
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
            "revoked_at": self.revoked_at,
            "used_at": self.used_at,
            "runtime_only": True,
            "survives_restart": False,
            "execution_performed": False,
        }


@dataclass(frozen=True, slots=True)
class ApprovalValidation:
    valid: bool
    status: str
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "status": self.status, "reasons": list(self.reasons)}


def approval_scope_from_evaluation(entry_id: str, evaluation: Mapping[str, Any]) -> ApprovalScope:
    """Build immutable scope only from a clean approval-required evaluation."""
    if str(evaluation.get("status")) != "approval_required":
        raise ValueError("execution evaluation must require approval")
    plan = evaluation.get("plan")
    policy = evaluation.get("policy")
    if not isinstance(plan, Mapping) or not isinstance(policy, Mapping):
        raise ValueError("execution evaluation must include plan and policy snapshots")
    entity_id = str(evaluation.get("entity_id") or "").strip()
    scope = ApprovalScope(
        entry_id=entry_id,
        profile_id=str(evaluation.get("profile_id") or ""),
        entity_id=entity_id,
        plan_starts_at=str(plan.get("starts_at") or ""),
        plan_ends_at=str(plan.get("ends_at") or ""),
        plan_power_kw=float(plan.get("power_kw", 0)),
        plan_duration_minutes=int(plan.get("duration_minutes", 0)),
        plan_average_czk_kwh=float(plan.get("average_czk_kwh", 0)),
        plan_estimated_cost_czk=float(plan.get("estimated_cost_czk", 0)),
        policy_mode=str(policy.get("mode") or ""),
        policy_max_power_kw=float(policy.get("max_power_kw", 0)),
        policy_max_duration_minutes=int(policy.get("max_duration_minutes", 0)),
        policy_require_entity_binding=bool(policy.get("require_entity_binding", True)),
        policy_require_entity_available=bool(policy.get("require_entity_available", True)),
    )
    return scope.validated()


def create_approval(
    entry_id: str,
    evaluation: Mapping[str, Any],
    *,
    ttl_seconds: int = APPROVAL_TTL_DEFAULT_SECONDS,
    now: datetime | None = None,
    approval_id: str | None = None,
) -> LoadExecutionApproval:
    """Create a pending approval request from an immutable clean evaluation."""
    if ttl_seconds < APPROVAL_TTL_MIN_SECONDS or ttl_seconds > APPROVAL_TTL_MAX_SECONDS:
        raise ValueError(
            f"ttl_seconds must be between {APPROVAL_TTL_MIN_SECONDS} and {APPROVAL_TTL_MAX_SECONDS}"
        )
    current = now or _utc_now()
    scope = approval_scope_from_evaluation(entry_id, evaluation)
    starts = _parse_aware(scope.plan_starts_at, "plan_starts_at")
    if current >= starts:
        raise ValueError("cannot create approval after plan start")
    requested_expiry = current + timedelta(seconds=ttl_seconds)
    expires = min(requested_expiry, starts)
    token = approval_id or secrets.token_urlsafe(24)
    return LoadExecutionApproval(
        approval_id=token,
        scope=scope,
        scope_hash=scope.fingerprint(),
        created_at=current.isoformat(),
        expires_at=expires.isoformat(),
    )


def validate_approval(
    approval: LoadExecutionApproval,
    *,
    current_scope_hash: str,
    now: datetime | None = None,
) -> ApprovalValidation:
    """Validate an approval without consuming it."""
    current = now or _utc_now()
    effective = approval.effective_status(current)
    reasons: list[str] = []
    if effective == APPROVAL_STATUS_USED:
        reasons.append(VALIDATION_ALREADY_USED)
    elif effective == APPROVAL_STATUS_REVOKED:
        reasons.append(VALIDATION_REVOKED)
    elif effective == APPROVAL_STATUS_EXPIRED:
        reasons.append(VALIDATION_EXPIRED)
    elif effective != APPROVAL_STATUS_APPROVED:
        reasons.append(VALIDATION_NOT_APPROVED)
    if approval.scope_hash != current_scope_hash:
        reasons.append(VALIDATION_SCOPE_CHANGED)
    if current >= _parse_aware(approval.scope.plan_starts_at, "plan_starts_at"):
        reasons.append(VALIDATION_PLAN_STARTED)
    return ApprovalValidation(valid=not reasons, status=effective, reasons=tuple(reasons))


def approve_approval(
    approval: LoadExecutionApproval,
    *,
    current_scope_hash: str,
    approved_by: str,
    now: datetime | None = None,
) -> LoadExecutionApproval:
    """Approve a pending request only if its current immutable scope still matches."""
    current = now or _utc_now()
    if approval.effective_status(current) != APPROVAL_STATUS_PENDING:
        raise ValueError("only a pending approval can be approved")
    if not approved_by.strip():
        raise ValueError("approved_by is required")
    if approval.scope_hash != current_scope_hash:
        raise ValueError("approval scope changed")
    if current >= _parse_aware(approval.scope.plan_starts_at, "plan_starts_at"):
        raise ValueError("approval plan has already started")
    return replace(
        approval,
        status=APPROVAL_STATUS_APPROVED,
        approved_at=current.isoformat(),
        approved_by=approved_by,
    )


def revoke_approval(
    approval: LoadExecutionApproval,
    *,
    now: datetime | None = None,
) -> LoadExecutionApproval:
    """Revoke a pending or approved request."""
    current = now or _utc_now()
    effective = approval.effective_status(current)
    if effective not in {APPROVAL_STATUS_PENDING, APPROVAL_STATUS_APPROVED}:
        raise ValueError("only pending or approved approval can be revoked")
    return replace(approval, status=APPROVAL_STATUS_REVOKED, revoked_at=current.isoformat())


def consume_approval(
    approval: LoadExecutionApproval,
    *,
    current_scope_hash: str,
    now: datetime | None = None,
) -> LoadExecutionApproval:
    """Atomically model one-time consumption for a future executor integration."""
    current = now or _utc_now()
    validation = validate_approval(approval, current_scope_hash=current_scope_hash, now=current)
    if not validation.valid:
        raise ValueError(f"approval is not consumable: {','.join(validation.reasons)}")
    return replace(approval, status=APPROVAL_STATUS_USED, used_at=current.isoformat())


class LoadExecutionApprovalRegistry:
    """In-memory approval registry; restart intentionally clears every approval."""

    def __init__(self) -> None:
        self._items: dict[str, LoadExecutionApproval] = {}

    def create(
        self,
        entry_id: str,
        evaluation: Mapping[str, Any],
        *,
        ttl_seconds: int = APPROVAL_TTL_DEFAULT_SECONDS,
        now: datetime | None = None,
    ) -> LoadExecutionApproval:
        approval = create_approval(entry_id, evaluation, ttl_seconds=ttl_seconds, now=now)
        self._items[approval.approval_id] = approval
        return approval

    def get(self, approval_id: str) -> LoadExecutionApproval:
        try:
            return self._items[approval_id]
        except KeyError as err:
            raise ValueError(f"approval not found: {approval_id}") from err

    def list(self, *, entry_id: str | None = None) -> tuple[LoadExecutionApproval, ...]:
        values = self._items.values()
        if entry_id is None:
            return tuple(values)
        return tuple(item for item in values if item.scope.entry_id == entry_id)

    def approve(
        self,
        approval_id: str,
        *,
        current_scope_hash: str,
        approved_by: str,
        now: datetime | None = None,
    ) -> LoadExecutionApproval:
        updated = approve_approval(
            self.get(approval_id),
            current_scope_hash=current_scope_hash,
            approved_by=approved_by,
            now=now,
        )
        self._items[approval_id] = updated
        return updated

    def revoke(self, approval_id: str, *, now: datetime | None = None) -> LoadExecutionApproval:
        updated = revoke_approval(self.get(approval_id), now=now)
        self._items[approval_id] = updated
        return updated

    def validate(
        self,
        approval_id: str,
        *,
        current_scope_hash: str,
        now: datetime | None = None,
    ) -> ApprovalValidation:
        return validate_approval(self.get(approval_id), current_scope_hash=current_scope_hash, now=now)

    def consume(
        self,
        approval_id: str,
        *,
        current_scope_hash: str,
        now: datetime | None = None,
    ) -> LoadExecutionApproval:
        updated = consume_approval(self.get(approval_id), current_scope_hash=current_scope_hash, now=now)
        self._items[approval_id] = updated
        return updated
