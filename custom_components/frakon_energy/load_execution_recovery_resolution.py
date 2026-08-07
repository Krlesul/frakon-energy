"""Read-only recovery resolution planning for FRAKON Energy.

This module never mutates an execution lifecycle and never performs a Home
Assistant service call. It only decides whether durable recovery evidence is
sufficient for a later explicit verification step.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .load_execution_lifecycle import (
    STATE_CANCELLED,
    STATE_DISPATCHED,
    STATE_DISPATCHING,
    STATE_FAILED,
    STATE_PREPARED,
    STATE_RECOVERY_REQUIRED,
    STATE_VERIFIED,
    ExecutionLifecycleRecord,
)

RESOLUTION_SAFE_TO_VERIFY = "safe_to_verify"
RESOLUTION_OPERATOR_ACTION_REQUIRED = "operator_action_required"
RESOLUTION_BLOCKED = "blocked"
RESOLUTION_NOT_APPLICABLE = "not_applicable"

REASON_DESIRED_STATE_OBSERVED = "desired_state_observed"
REASON_DESIRED_STATE_NOT_OBSERVED = "desired_state_not_observed"
REASON_ENTITY_STATE_UNAVAILABLE = "entity_state_unavailable"
REASON_INTERRUPTED_DISPATCH_NOT_RECOVERED = "interrupted_dispatch_not_recovered"
REASON_PREPARED_NOT_DISPATCHED = "prepared_not_dispatched"
REASON_ALREADY_VERIFIED = "already_verified"
REASON_TERMINAL_WITHOUT_VERIFICATION = "terminal_without_verification"

_UNAVAILABLE_STATES = {None, "unknown", "unavailable"}


@dataclass(frozen=True, slots=True)
class RecoveryResolutionDecision:
    """Read-only decision for one durable lifecycle recovery state."""

    status: str
    reason: str
    lifecycle_id: str
    attempt_id: str
    lifecycle_state: str
    entity_id: str
    current_state: str | None
    desired_state: str
    service_call_status: str
    verification_status: str
    can_mark_verified: bool
    can_redispatch: bool
    manual_review_required: bool
    resolution_performed: bool = False
    state_transition_performed: bool = False
    execution_performed: bool = False
    executor_available: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _normalized_state(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def _decision(
    record: ExecutionLifecycleRecord,
    *,
    status: str,
    reason: str,
    current_state: str | None,
    can_mark_verified: bool = False,
    manual_review_required: bool = False,
) -> RecoveryResolutionDecision:
    return RecoveryResolutionDecision(
        status=status,
        reason=reason,
        lifecycle_id=record.lifecycle_id,
        attempt_id=record.attempt_id,
        lifecycle_state=record.state,
        entity_id=record.entity_id,
        current_state=current_state,
        desired_state=record.desired_state,
        service_call_status=record.service_call_status,
        verification_status=record.verification_status,
        can_mark_verified=can_mark_verified,
        can_redispatch=False,
        manual_review_required=manual_review_required,
    )


def evaluate_recovery_resolution(
    record: ExecutionLifecycleRecord,
    *,
    current_state: str | None,
) -> RecoveryResolutionDecision:
    """Plan recovery resolution without changing lifecycle state.

    ``safe_to_verify`` means only that a later isolated verification operation
    may mark the durable desired state as observed. It does not prove that the
    FRAKON service call caused the observed state, and it never authorizes a
    redispatch.
    """
    record.validated()
    normalized = _normalized_state(current_state)

    if record.state == STATE_DISPATCHING:
        return _decision(
            record,
            status=RESOLUTION_BLOCKED,
            reason=REASON_INTERRUPTED_DISPATCH_NOT_RECOVERED,
            current_state=normalized,
            manual_review_required=True,
        )

    if record.state == STATE_PREPARED:
        return _decision(
            record,
            status=RESOLUTION_NOT_APPLICABLE,
            reason=REASON_PREPARED_NOT_DISPATCHED,
            current_state=normalized,
        )

    if record.state == STATE_VERIFIED:
        return _decision(
            record,
            status=RESOLUTION_NOT_APPLICABLE,
            reason=REASON_ALREADY_VERIFIED,
            current_state=normalized,
        )

    if record.state in {STATE_FAILED, STATE_CANCELLED}:
        return _decision(
            record,
            status=RESOLUTION_NOT_APPLICABLE,
            reason=REASON_TERMINAL_WITHOUT_VERIFICATION,
            current_state=normalized,
        )

    if record.state not in {STATE_RECOVERY_REQUIRED, STATE_DISPATCHED}:
        return _decision(
            record,
            status=RESOLUTION_BLOCKED,
            reason=REASON_INTERRUPTED_DISPATCH_NOT_RECOVERED,
            current_state=normalized,
            manual_review_required=True,
        )

    if normalized in _UNAVAILABLE_STATES:
        return _decision(
            record,
            status=RESOLUTION_BLOCKED,
            reason=REASON_ENTITY_STATE_UNAVAILABLE,
            current_state=normalized,
            manual_review_required=True,
        )

    if normalized == record.desired_state:
        return _decision(
            record,
            status=RESOLUTION_SAFE_TO_VERIFY,
            reason=REASON_DESIRED_STATE_OBSERVED,
            current_state=normalized,
            can_mark_verified=True,
        )

    return _decision(
        record,
        status=RESOLUTION_OPERATOR_ACTION_REQUIRED,
        reason=REASON_DESIRED_STATE_NOT_OBSERVED,
        current_state=normalized,
        manual_review_required=True,
    )
