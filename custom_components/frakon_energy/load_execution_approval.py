"""Short-lived one-time approval artifacts for future FRAKON Energy execution.

This module contains no executor and no Home Assistant service calls. It only
issues and validates signed approval artifacts for an exact immutable snapshot
of a profile, plan, entity binding and execution policy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import hmac
import json
import secrets
from typing import Any

from .energy_load_planner import LoadPlan
from .load_execution_policy import (
    DECISION_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
    evaluate_execution_policy,
)
from .load_profiles import LoadProfile

APPROVAL_SCHEMA_VERSION = 1
APPROVAL_INTENT_EXECUTE_LOAD_PLAN = "execute_load_plan"
DEFAULT_APPROVAL_TTL_SECONDS = 120
MAX_APPROVAL_TTL_SECONDS = 300

VERIFY_OK = "ok"
VERIFY_UNKNOWN_APPROVAL = "unknown_approval"
VERIFY_REPLAYED = "replayed"
VERIFY_REVOKED = "revoked"
VERIFY_EXPIRED = "expired"
VERIFY_NOT_YET_VALID = "not_yet_valid"
VERIFY_INVALID_SIGNATURE = "invalid_signature"
VERIFY_SNAPSHOT_MISMATCH = "snapshot_mismatch"
VERIFY_POLICY_NOT_ELIGIBLE = "policy_not_eligible"


def _aware_timestamp(value: datetime, field: str) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return int(value.timestamp())


def _plan_timestamp(value: str, field: str) -> int:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"{field} must be an ISO-8601 datetime") from err
    return _aware_timestamp(parsed, field)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _snapshot_payload(profile: LoadProfile, plan: LoadPlan, policy: LoadExecutionPolicy) -> dict[str, Any]:
    return {
        "schema_version": APPROVAL_SCHEMA_VERSION,
        "intent": APPROVAL_INTENT_EXECUTE_LOAD_PLAN,
        "profile": profile.as_dict(),
        "plan": plan.as_dict(),
        "policy": policy.as_dict(),
    }


def execution_snapshot_digest(profile: LoadProfile, plan: LoadPlan, policy: LoadExecutionPolicy) -> str:
    """Return a deterministic digest for the exact execution candidate."""
    profile.validated()
    policy.validated()
    return hashlib.sha256(_canonical_json(_snapshot_payload(profile, plan, policy))).hexdigest()


@dataclass(frozen=True, slots=True)
class ExecutionApproval:
    """Signed approval for one exact candidate and a short validity window."""

    approval_id: str
    intent: str
    snapshot_digest: str
    issued_at: int
    expires_at: int
    signature: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ApprovalVerification:
    """Read-only approval validation result."""

    valid: bool
    reason: str
    approval_id: str
    snapshot_digest: str
    consumed: bool
    execution_performed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ApprovalAuthority:
    """Issue, revoke and consume ephemeral HMAC approvals.

    The secret and replay/revocation registries are intentionally process-local.
    Creating a new authority after restart invalidates all outstanding approvals,
    which is the fail-closed behavior until durable approval state is designed.
    """

    def __init__(self, secret: bytes) -> None:
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("approval secret must contain at least 32 bytes")
        self._secret = secret
        self._issued_ids: set[str] = set()
        self._consumed_ids: set[str] = set()
        self._revoked_ids: set[str] = set()

    @classmethod
    def ephemeral(cls) -> "ApprovalAuthority":
        """Create an authority whose approvals become invalid after restart."""
        return cls(secrets.token_bytes(32))

    def _signature_payload(
        self,
        *,
        approval_id: str,
        snapshot_digest: str,
        issued_at: int,
        expires_at: int,
    ) -> bytes:
        return _canonical_json(
            {
                "schema_version": APPROVAL_SCHEMA_VERSION,
                "approval_id": approval_id,
                "intent": APPROVAL_INTENT_EXECUTE_LOAD_PLAN,
                "snapshot_digest": snapshot_digest,
                "issued_at": issued_at,
                "expires_at": expires_at,
            }
        )

    def _sign(
        self,
        *,
        approval_id: str,
        snapshot_digest: str,
        issued_at: int,
        expires_at: int,
    ) -> str:
        return hmac.new(
            self._secret,
            self._signature_payload(
                approval_id=approval_id,
                snapshot_digest=snapshot_digest,
                issued_at=issued_at,
                expires_at=expires_at,
            ),
            hashlib.sha256,
        ).hexdigest()

    def issue(
        self,
        profile: LoadProfile,
        plan: LoadPlan,
        policy: LoadExecutionPolicy,
        *,
        entity_available: bool | None,
        now: datetime,
        ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
    ) -> ExecutionApproval:
        """Issue an approval only for a candidate currently requiring approval.

        Approval validity is capped at the plan start. An approval can therefore
        never authorize a late start after its immutable planned window begins.
        """
        issued_at = _aware_timestamp(now, "now")
        if ttl_seconds <= 0 or ttl_seconds > MAX_APPROVAL_TTL_SECONDS:
            raise ValueError(f"ttl_seconds must be between 1 and {MAX_APPROVAL_TTL_SECONDS}")

        plan_starts_at = _plan_timestamp(plan.starts_at, "plan.starts_at")
        if issued_at >= plan_starts_at:
            raise ValueError("cannot issue approval after the plan has started")

        decision = evaluate_execution_policy(
            profile,
            plan,
            policy,
            entity_available=entity_available,
        )
        if decision.status != DECISION_APPROVAL_REQUIRED or decision.reasons:
            raise ValueError("execution candidate is not eligible for approval")

        approval_id = secrets.token_urlsafe(18)
        while approval_id in self._issued_ids:
            approval_id = secrets.token_urlsafe(18)
        snapshot_digest = execution_snapshot_digest(profile, plan, policy)
        expires_at = min(issued_at + ttl_seconds, plan_starts_at)
        signature = self._sign(
            approval_id=approval_id,
            snapshot_digest=snapshot_digest,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        self._issued_ids.add(approval_id)
        return ExecutionApproval(
            approval_id=approval_id,
            intent=APPROVAL_INTENT_EXECUTE_LOAD_PLAN,
            snapshot_digest=snapshot_digest,
            issued_at=issued_at,
            expires_at=expires_at,
            signature=signature,
        )

    def verify(
        self,
        approval: ExecutionApproval,
        profile: LoadProfile,
        plan: LoadPlan,
        policy: LoadExecutionPolicy,
        *,
        entity_available: bool | None,
        now: datetime,
    ) -> ApprovalVerification:
        """Validate an approval without consuming it or performing execution."""
        now_ts = _aware_timestamp(now, "now")
        if not all(
            (
                isinstance(approval.approval_id, str),
                isinstance(approval.intent, str),
                isinstance(approval.snapshot_digest, str),
                isinstance(approval.issued_at, int),
                isinstance(approval.expires_at, int),
                isinstance(approval.signature, str),
            )
        ):
            return self._result(approval, False, VERIFY_INVALID_SIGNATURE)
        if approval.approval_id not in self._issued_ids:
            return self._result(approval, False, VERIFY_UNKNOWN_APPROVAL)
        if approval.approval_id in self._revoked_ids:
            return self._result(approval, False, VERIFY_REVOKED)
        if approval.approval_id in self._consumed_ids:
            return self._result(approval, False, VERIFY_REPLAYED, consumed=True)
        if approval.intent != APPROVAL_INTENT_EXECUTE_LOAD_PLAN:
            return self._result(approval, False, VERIFY_INVALID_SIGNATURE)
        if approval.expires_at <= approval.issued_at:
            return self._result(approval, False, VERIFY_INVALID_SIGNATURE)

        expected_signature = self._sign(
            approval_id=approval.approval_id,
            snapshot_digest=approval.snapshot_digest,
            issued_at=approval.issued_at,
            expires_at=approval.expires_at,
        )
        if not hmac.compare_digest(approval.signature, expected_signature):
            return self._result(approval, False, VERIFY_INVALID_SIGNATURE)
        if now_ts < approval.issued_at:
            return self._result(approval, False, VERIFY_NOT_YET_VALID)
        if now_ts >= approval.expires_at:
            return self._result(approval, False, VERIFY_EXPIRED)

        try:
            current_digest = execution_snapshot_digest(profile, plan, policy)
        except (TypeError, ValueError):
            return self._result(approval, False, VERIFY_POLICY_NOT_ELIGIBLE)
        if not hmac.compare_digest(approval.snapshot_digest, current_digest):
            return self._result(approval, False, VERIFY_SNAPSHOT_MISMATCH)

        try:
            decision = evaluate_execution_policy(
                profile,
                plan,
                policy,
                entity_available=entity_available,
            )
        except (TypeError, ValueError):
            return self._result(approval, False, VERIFY_POLICY_NOT_ELIGIBLE)
        if decision.status != DECISION_APPROVAL_REQUIRED or decision.reasons:
            return self._result(approval, False, VERIFY_POLICY_NOT_ELIGIBLE)
        return self._result(approval, True, VERIFY_OK)

    def revoke(self, approval: ExecutionApproval) -> ApprovalVerification:
        """Revoke a known, not-yet-consumed approval without execution."""
        if approval.approval_id not in self._issued_ids:
            return self._result(approval, False, VERIFY_UNKNOWN_APPROVAL)
        if approval.approval_id in self._consumed_ids:
            return self._result(approval, False, VERIFY_REPLAYED, consumed=True)
        self._revoked_ids.add(approval.approval_id)
        return self._result(approval, False, VERIFY_REVOKED)

    def consume(
        self,
        approval: ExecutionApproval,
        profile: LoadProfile,
        plan: LoadPlan,
        policy: LoadExecutionPolicy,
        *,
        entity_available: bool | None,
        now: datetime,
    ) -> ApprovalVerification:
        """Consume a valid approval exactly once, without executing any action."""
        verification = self.verify(
            approval,
            profile,
            plan,
            policy,
            entity_available=entity_available,
            now=now,
        )
        if not verification.valid:
            return verification
        self._consumed_ids.add(approval.approval_id)
        return self._result(approval, True, VERIFY_OK, consumed=True)

    @staticmethod
    def _result(
        approval: ExecutionApproval,
        valid: bool,
        reason: str,
        *,
        consumed: bool = False,
    ) -> ApprovalVerification:
        return ApprovalVerification(
            valid=valid,
            reason=reason,
            approval_id=approval.approval_id,
            snapshot_digest=approval.snapshot_digest,
            consumed=consumed,
            execution_performed=False,
        )
