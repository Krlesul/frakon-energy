"""Final bounded-start gate requiring a durable stop obligation.

This layer composes the existing read-only dispatch gate with an immutable armed
stop lease. It never mutates lifecycle state and never calls Home Assistant.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .load_execution_dispatch_gate import (
    DISPATCH_GATE_ALREADY_SATISFIED,
    DISPATCH_GATE_READY,
    DispatchGateDecision,
)
from .load_execution_lifecycle import ExecutionLifecycleRecord
from .load_execution_stop_lease import ExecutionStopLease, STOP_LEASE_ARMED

BOUNDED_GATE_READY = "ready_to_start"
BOUNDED_GATE_ALREADY_SATISFIED = "already_satisfied"
BOUNDED_GATE_BLOCKED = "blocked"

REASON_READY = "bounded_start_has_armed_stop_obligation"
REASON_ALREADY_SATISFIED = "desired_state_already_observed"
REASON_DISPATCH_GATE_MISMATCH = "dispatch_gate_binding_mismatch"
REASON_STOP_LEASE_REQUIRED = "matching_stop_lease_required"
REASON_STOP_LEASE_MISMATCH = "stop_lease_binding_mismatch"

_EXPECTED_STOP_MAPPING: dict[tuple[str, str], tuple[str, str]] = {
    ("switch", "turn_on"): ("switch", "turn_off"),
    ("input_boolean", "turn_on"): ("input_boolean", "turn_off"),
}


@dataclass(frozen=True, slots=True)
class BoundedDispatchDecision:
    """Read-only decision immediately before a future bounded start executor."""

    status: str
    reason: str
    lifecycle_id: str
    attempt_id: str
    entity_id: str
    start_service_domain: str
    start_service_name: str
    stop_lease_id: str | None
    stop_intent_id: str | None
    stop_service_domain: str | None
    stop_service_name: str | None
    stop_at: str
    dispatch_gate_status: str
    dispatch_gate_matches: bool
    stop_lease_matches: bool
    can_start: bool
    can_redispatch: bool = False
    state_transition_performed: bool = False
    service_call_performed: bool = False
    execution_performed: bool = False
    executor_available: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _dispatch_gate_matches(
    lifecycle: ExecutionLifecycleRecord,
    gate: DispatchGateDecision,
) -> bool:
    return (
        gate.lifecycle_id == lifecycle.lifecycle_id
        and gate.lifecycle_state == lifecycle.state
        and gate.attempt_id == lifecycle.attempt_id
        and gate.action_snapshot_id == lifecycle.action_snapshot_id
        and gate.profile_id == lifecycle.profile_id
        and gate.entity_id == lifecycle.entity_id
        and gate.service_domain == lifecycle.service_domain
        and gate.service_name == lifecycle.service_name
        and gate.desired_state == lifecycle.desired_state
        and gate.plan_starts_at == lifecycle.plan.starts_at
        and gate.plan_ends_at == lifecycle.plan.ends_at
        and gate.lifecycle_binding_matches
        and not gate.can_redispatch
        and not gate.state_transition_performed
        and not gate.service_call_performed
        and not gate.execution_performed
        and not gate.executor_available
    )


def _lease_matches(
    lifecycle: ExecutionLifecycleRecord,
    lease: ExecutionStopLease,
) -> bool:
    try:
        lifecycle.validated()
        lease.validated()
    except ValueError:
        return False
    expected_stop = _EXPECTED_STOP_MAPPING.get(
        (lifecycle.service_domain, lifecycle.service_name)
    )
    return (
        expected_stop is not None
        and lease.status == STOP_LEASE_ARMED
        and lease.entry_id == lifecycle.entry_id
        and lease.lifecycle_id == lifecycle.lifecycle_id
        and lease.attempt_id == lifecycle.attempt_id
        and lease.action_snapshot_id == lifecycle.action_snapshot_id
        and lease.profile_id == lifecycle.profile_id
        and lease.entity_id == lifecycle.entity_id
        and lease.approval_snapshot_digest == lifecycle.approval_snapshot_digest
        and lease.plan_digest == lifecycle.plan_digest
        and lease.starts_at == lifecycle.plan.starts_at
        and lease.ends_at == lifecycle.plan.ends_at
        and (lease.service_domain, lease.service_name) == expected_stop
        and lease.desired_state == "off"
    )


def evaluate_bounded_dispatch_gate(
    *,
    lifecycle: ExecutionLifecycleRecord,
    dispatch_gate: DispatchGateDecision,
    stop_lease: ExecutionStopLease | None,
) -> BoundedDispatchDecision:
    """Require exact dispatch evidence and a stop obligation before future start."""
    lifecycle.validated()
    dispatch_gate_matches = _dispatch_gate_matches(lifecycle, dispatch_gate)
    lease_matches = stop_lease is not None and _lease_matches(lifecycle, stop_lease)
    base = dict(
        lifecycle_id=lifecycle.lifecycle_id,
        attempt_id=lifecycle.attempt_id,
        entity_id=lifecycle.entity_id,
        start_service_domain=lifecycle.service_domain,
        start_service_name=lifecycle.service_name,
        stop_lease_id=stop_lease.lease_id if stop_lease is not None else None,
        stop_intent_id=stop_lease.stop_intent_id if stop_lease is not None else None,
        stop_service_domain=stop_lease.service_domain if stop_lease is not None else None,
        stop_service_name=stop_lease.service_name if stop_lease is not None else None,
        stop_at=lifecycle.plan.ends_at,
        dispatch_gate_status=dispatch_gate.status,
        dispatch_gate_matches=dispatch_gate_matches,
        stop_lease_matches=lease_matches,
    )

    if not dispatch_gate_matches:
        return BoundedDispatchDecision(
            status=BOUNDED_GATE_BLOCKED,
            reason=REASON_DISPATCH_GATE_MISMATCH,
            can_start=False,
            **base,
        )

    if dispatch_gate.status == DISPATCH_GATE_ALREADY_SATISFIED:
        return BoundedDispatchDecision(
            status=BOUNDED_GATE_ALREADY_SATISFIED,
            reason=REASON_ALREADY_SATISFIED,
            can_start=False,
            **base,
        )

    if dispatch_gate.status != DISPATCH_GATE_READY or not dispatch_gate.can_dispatch:
        return BoundedDispatchDecision(
            status=BOUNDED_GATE_BLOCKED,
            reason=dispatch_gate.reason,
            can_start=False,
            **base,
        )

    if stop_lease is None:
        return BoundedDispatchDecision(
            status=BOUNDED_GATE_BLOCKED,
            reason=REASON_STOP_LEASE_REQUIRED,
            can_start=False,
            **base,
        )

    if not lease_matches:
        return BoundedDispatchDecision(
            status=BOUNDED_GATE_BLOCKED,
            reason=REASON_STOP_LEASE_MISMATCH,
            can_start=False,
            **base,
        )

    return BoundedDispatchDecision(
        status=BOUNDED_GATE_READY,
        reason=REASON_READY,
        can_start=True,
        **base,
    )
