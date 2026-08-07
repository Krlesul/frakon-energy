"""Dry-run execution preflight for FRAKON Energy.

This layer converts one valid signed approval into an idempotent execution
attempt and a proposed Home Assistant service call. It intentionally never
calls Home Assistant services and never consumes the approval.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from .load_execution_attempt import (
    ACTION_START,
    ExecutionAttemptLedger,
    LoadExecutionAttempt,
    create_execution_attempt,
)

PREFLIGHT_READY = "ready"
PREFLIGHT_BLOCKED = "blocked"

REASON_APPROVAL_INVALID = "approval_invalid"
REASON_ENTITY_REQUIRED = "entity_required"
REASON_UNSUPPORTED_ENTITY_DOMAIN = "unsupported_entity_domain"

_SUPPORTED_START_SERVICES: dict[str, tuple[str, str]] = {
    "switch": ("switch", "turn_on"),
    "input_boolean": ("input_boolean", "turn_on"),
}


@dataclass(frozen=True, slots=True)
class DryRunServiceProposal:
    """A Home Assistant service proposal that is never executed here."""

    domain: str
    service: str
    entity_id: str
    service_data: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExecutionPreflight:
    """Fail-closed dry-run result for one signed approval."""

    status: str
    reasons: tuple[str, ...]
    attempt: LoadExecutionAttempt | None
    proposal: DryRunServiceProposal | None
    approval_id: str
    approval_verification_reason: str
    dry_run: bool = True
    approval_consumed: bool = False
    execution_performed: bool = False
    executor_available: bool = False
    can_execute: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "attempt": None if self.attempt is None else self.attempt.as_dict(),
            "proposal": None if self.proposal is None else self.proposal.as_dict(),
            "approval_id": self.approval_id,
            "approval_verification_reason": self.approval_verification_reason,
            "dry_run": self.dry_run,
            "approval_consumed": self.approval_consumed,
            "execution_performed": self.execution_performed,
            "executor_available": self.executor_available,
            "can_execute": self.can_execute,
        }


def propose_start_service(entity_id: str | None) -> DryRunServiceProposal:
    """Map a deliberately small set of entity domains to a dry-run start action."""
    if not entity_id or "." not in entity_id:
        raise ValueError(REASON_ENTITY_REQUIRED)
    domain = entity_id.split(".", 1)[0]
    mapping = _SUPPORTED_START_SERVICES.get(domain)
    if mapping is None:
        raise ValueError(REASON_UNSUPPORTED_ENTITY_DOMAIN)
    service_domain, service = mapping
    return DryRunServiceProposal(
        domain=service_domain,
        service=service,
        entity_id=entity_id,
        service_data={"entity_id": entity_id},
    )


def prepare_execution_preflight(
    ledger: ExecutionAttemptLedger,
    *,
    approval_id: str,
    snapshot_digest: str,
    profile_id: str,
    entity_id: str | None,
    planned_starts_at: str,
    planned_ends_at: str,
    approval_valid: bool,
    approval_verification_reason: str,
    now: datetime,
) -> ExecutionPreflight:
    """Create or deduplicate a prepared attempt after approval verification."""
    if not approval_valid:
        return ExecutionPreflight(
            status=PREFLIGHT_BLOCKED,
            reasons=(REASON_APPROVAL_INVALID,),
            attempt=None,
            proposal=None,
            approval_id=approval_id,
            approval_verification_reason=approval_verification_reason,
        )

    try:
        proposal = propose_start_service(entity_id)
    except ValueError as err:
        return ExecutionPreflight(
            status=PREFLIGHT_BLOCKED,
            reasons=(str(err),),
            attempt=None,
            proposal=None,
            approval_id=approval_id,
            approval_verification_reason=approval_verification_reason,
        )

    attempt = create_execution_attempt(
        approval_id=approval_id,
        snapshot_digest=snapshot_digest,
        profile_id=profile_id,
        entity_id=proposal.entity_id,
        action=ACTION_START,
        planned_starts_at=planned_starts_at,
        planned_ends_at=planned_ends_at,
        now=now,
    )
    registered = ledger.register(attempt)
    return ExecutionPreflight(
        status=PREFLIGHT_READY,
        reasons=(),
        attempt=registered,
        proposal=proposal,
        approval_id=approval_id,
        approval_verification_reason=approval_verification_reason,
    )
