"""Final read-only dispatch gate for FRAKON Energy.

The gate binds current execution readiness to one durable prepared lifecycle.
It never performs a Home Assistant service call and never mutates lifecycle state.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .load_execution_action_snapshot import ExecutionActionSnapshot
from .load_execution_attempt import ExecutionAttempt
from .load_execution_lifecycle import STATE_PREPARED, ExecutionLifecycleRecord
from .load_execution_readiness import (
    READINESS_ALREADY_SATISFIED,
    READINESS_READY,
    ExecutionReadinessDecision,
)

DISPATCH_GATE_READY = "ready_to_dispatch"
DISPATCH_GATE_ALREADY_SATISFIED = "already_satisfied"
DISPATCH_GATE_BLOCKED = "blocked"

REASON_READY = "durable_prepared_lifecycle_is_ready"
REASON_ALREADY_SATISFIED = "desired_state_already_observed"
REASON_LIFECYCLE_NOT_PREPARED = "lifecycle_not_prepared"
REASON_LIFECYCLE_BINDING_CHANGED = "lifecycle_binding_changed"
REASON_READINESS_EVIDENCE_INVALID = "readiness_evidence_invalid"


@dataclass(frozen=True, slots=True)
class DispatchGateDecision:
    """Read-only final decision immediately before a future dispatcher."""

    status: str
    reason: str
    lifecycle_id: str
    lifecycle_state: str
    attempt_id: str
    action_snapshot_id: str
    profile_id: str
    entity_id: str
    service_domain: str
    service_name: str
    desired_state: str
    current_state: str | None
    plan_starts_at: str
    plan_ends_at: str
    readiness_status: str
    readiness_reason: str
    lifecycle_binding_matches: bool
    can_dispatch: bool
    can_redispatch: bool = False
    state_transition_performed: bool = False
    service_call_performed: bool = False
    execution_performed: bool = False
    executor_available: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _binding_matches(
    lifecycle: ExecutionLifecycleRecord,
    attempt: ExecutionAttempt,
    snapshot: ExecutionActionSnapshot,
    readiness: ExecutionReadinessDecision,
) -> bool:
    return (
        lifecycle.entry_id == attempt.entry_id == snapshot.entry_id
        and lifecycle.attempt_id == attempt.attempt_id
        and lifecycle.action_snapshot_id == snapshot.snapshot_id
        and lifecycle.profile_id == attempt.profile_id == snapshot.profile_id
        and lifecycle.entity_id == attempt.entity_id == snapshot.entity_id
        and lifecycle.approval_snapshot_digest == attempt.snapshot_digest
        and lifecycle.plan.load_id == lifecycle.profile_id
        and lifecycle.service_domain == snapshot.service_domain
        and lifecycle.service_name == snapshot.service_name
        and lifecycle.desired_state == snapshot.desired_state
        and readiness.attempt_id == attempt.attempt_id
        and readiness.action_snapshot_id == snapshot.snapshot_id
        and readiness.profile_id == lifecycle.profile_id
        and readiness.entity_id == lifecycle.entity_id
        and readiness.desired_state == lifecycle.desired_state
        and readiness.plan_starts_at == lifecycle.plan.starts_at
        and readiness.plan_ends_at == lifecycle.plan.ends_at
    )


def evaluate_dispatch_gate(
    *,
    lifecycle: ExecutionLifecycleRecord,
    attempt: ExecutionAttempt,
    snapshot: ExecutionActionSnapshot,
    readiness: ExecutionReadinessDecision,
) -> DispatchGateDecision:
    """Bind current readiness to one durable prepared lifecycle without mutation."""
    lifecycle.validated()
    attempt.validated()
    snapshot.validated()

    binding_matches = _binding_matches(lifecycle, attempt, snapshot, readiness)
    base = dict(
        lifecycle_id=lifecycle.lifecycle_id,
        lifecycle_state=lifecycle.state,
        attempt_id=attempt.attempt_id,
        action_snapshot_id=snapshot.snapshot_id,
        profile_id=lifecycle.profile_id,
        entity_id=lifecycle.entity_id,
        service_domain=lifecycle.service_domain,
        service_name=lifecycle.service_name,
        desired_state=lifecycle.desired_state,
        current_state=readiness.current_state,
        plan_starts_at=lifecycle.plan.starts_at,
        plan_ends_at=lifecycle.plan.ends_at,
        readiness_status=readiness.status,
        readiness_reason=readiness.reason,
        lifecycle_binding_matches=binding_matches,
    )

    if lifecycle.state != STATE_PREPARED:
        return DispatchGateDecision(
            status=DISPATCH_GATE_BLOCKED,
            reason=REASON_LIFECYCLE_NOT_PREPARED,
            can_dispatch=False,
            **base,
        )

    if not binding_matches:
        return DispatchGateDecision(
            status=DISPATCH_GATE_BLOCKED,
            reason=REASON_LIFECYCLE_BINDING_CHANGED,
            can_dispatch=False,
            **base,
        )

    if readiness.execution_performed or readiness.service_call_performed or readiness.executor_available:
        return DispatchGateDecision(
            status=DISPATCH_GATE_BLOCKED,
            reason=REASON_READINESS_EVIDENCE_INVALID,
            can_dispatch=False,
            **base,
        )

    if readiness.status == READINESS_READY and readiness.action_required:
        return DispatchGateDecision(
            status=DISPATCH_GATE_READY,
            reason=REASON_READY,
            can_dispatch=True,
            **base,
        )

    if readiness.status == READINESS_ALREADY_SATISFIED and not readiness.action_required:
        return DispatchGateDecision(
            status=DISPATCH_GATE_ALREADY_SATISFIED,
            reason=REASON_ALREADY_SATISFIED,
            can_dispatch=False,
            **base,
        )

    return DispatchGateDecision(
        status=DISPATCH_GATE_BLOCKED,
        reason=readiness.reason,
        can_dispatch=False,
        **base,
    )
