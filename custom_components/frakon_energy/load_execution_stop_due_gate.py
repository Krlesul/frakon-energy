"""Read-only deadline gate for durable bounded stop execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from .load_execution_stop_lifecycle import (
    STOP_STATE_DISPATCHED,
    STOP_STATE_DISPATCHING,
    STOP_STATE_FAILED,
    STOP_STATE_OWNED,
    STOP_STATE_RECOVERY_REQUIRED,
    STOP_STATE_SATISFIED,
    STOP_STATE_VERIFIED,
    ExecutionStopLifecycleRecord,
)

STOP_DUE_WAITING = "waiting"
STOP_DUE_READY = "ready_to_stop"
STOP_DUE_ALREADY_OFF = "already_off"
STOP_DUE_SAFE_TO_VERIFY = "safe_to_verify"
STOP_DUE_RECOVERY_REVIEW = "recovery_review"
STOP_DUE_COMPLETED = "completed"
STOP_DUE_BLOCKED = "blocked"

REASON_WAITING = "stop_deadline_in_future"
REASON_READY = "stop_deadline_reached_entity_on"
REASON_ALREADY_OFF = "stop_deadline_reached_entity_already_off"
REASON_SAFE_TO_VERIFY = "stop_desired_state_observed_after_dispatch"
REASON_RECOVERY_REVIEW = "unknown_stop_outcome_entity_still_on"
REASON_RECOVERY_UNHEALTHY = "stop_startup_recovery_not_ready"
REASON_ENTITY_UNAVAILABLE = "stop_entity_state_unavailable"
REASON_DISPATCHING_UNRECOVERED = "stop_dispatching_requires_startup_recovery"
REASON_TERMINAL = "stop_lifecycle_terminal"
REASON_FAILED = "stop_lifecycle_failed"
REASON_DISPATCH_CONFIRMED_STATE_ON = "stop_dispatch_confirmed_but_entity_still_on"


class StopDueGateError(ValueError):
    """Raised when stop due evaluation input is invalid."""


@dataclass(frozen=True, slots=True)
class StopDueDecision:
    status: str
    reason: str
    stop_lifecycle_id: str
    start_lifecycle_id: str
    entity_id: str
    current_state: str | None
    desired_state: str
    ends_at: str
    seconds_until_due: int
    recovery_ready: bool
    can_dispatch_stop: bool
    can_complete_noop: bool
    can_mark_verified: bool
    can_retry_unknown: bool = False
    state_transition_performed: bool = False
    service_call_performed: bool = False
    execution_performed: bool = False
    executor_available: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _normalized_state(value: str | None) -> str | None:
    return value.strip().lower() if isinstance(value, str) else None


def _decision(
    record: ExecutionStopLifecycleRecord,
    *,
    status: str,
    reason: str,
    current_state: str | None,
    now: datetime,
    recovery_ready: bool,
    can_dispatch_stop: bool = False,
    can_complete_noop: bool = False,
    can_mark_verified: bool = False,
) -> StopDueDecision:
    due = datetime.fromisoformat(record.ends_at)
    return StopDueDecision(
        status=status,
        reason=reason,
        stop_lifecycle_id=record.stop_lifecycle_id,
        start_lifecycle_id=record.start_lifecycle_id,
        entity_id=record.entity_id,
        current_state=_normalized_state(current_state),
        desired_state=record.desired_state,
        ends_at=record.ends_at,
        seconds_until_due=int(due.timestamp()) - int(now.timestamp()),
        recovery_ready=recovery_ready,
        can_dispatch_stop=can_dispatch_stop,
        can_complete_noop=can_complete_noop,
        can_mark_verified=can_mark_verified,
    )


def evaluate_stop_due_gate(
    *,
    record: ExecutionStopLifecycleRecord,
    current_state: str | None,
    now: datetime,
    recovery_ready: bool,
) -> StopDueDecision:
    """Evaluate whether a durable stop obligation is due without mutation."""
    record.validated()
    if now.tzinfo is None or now.utcoffset() is None:
        raise StopDueGateError("now must be timezone-aware")
    due = datetime.fromisoformat(record.ends_at)
    normalized = _normalized_state(current_state)

    if record.state in (STOP_STATE_VERIFIED, STOP_STATE_SATISFIED):
        return _decision(
            record,
            status=STOP_DUE_COMPLETED,
            reason=REASON_TERMINAL,
            current_state=current_state,
            now=now,
            recovery_ready=recovery_ready,
        )
    if record.state == STOP_STATE_FAILED:
        return _decision(
            record,
            status=STOP_DUE_BLOCKED,
            reason=REASON_FAILED,
            current_state=current_state,
            now=now,
            recovery_ready=recovery_ready,
        )
    if not recovery_ready:
        return _decision(
            record,
            status=STOP_DUE_BLOCKED,
            reason=REASON_RECOVERY_UNHEALTHY,
            current_state=current_state,
            now=now,
            recovery_ready=False,
        )
    if record.state == STOP_STATE_DISPATCHING:
        return _decision(
            record,
            status=STOP_DUE_BLOCKED,
            reason=REASON_DISPATCHING_UNRECOVERED,
            current_state=current_state,
            now=now,
            recovery_ready=True,
        )
    if normalized in (None, "unknown", "unavailable"):
        return _decision(
            record,
            status=STOP_DUE_BLOCKED,
            reason=REASON_ENTITY_UNAVAILABLE,
            current_state=current_state,
            now=now,
            recovery_ready=True,
        )

    if record.state == STOP_STATE_DISPATCHED:
        if normalized == record.desired_state:
            return _decision(
                record,
                status=STOP_DUE_SAFE_TO_VERIFY,
                reason=REASON_SAFE_TO_VERIFY,
                current_state=current_state,
                now=now,
                recovery_ready=True,
                can_mark_verified=True,
            )
        return _decision(
            record,
            status=STOP_DUE_BLOCKED,
            reason=REASON_DISPATCH_CONFIRMED_STATE_ON,
            current_state=current_state,
            now=now,
            recovery_ready=True,
        )

    if record.state == STOP_STATE_RECOVERY_REQUIRED:
        if normalized == record.desired_state:
            return _decision(
                record,
                status=STOP_DUE_SAFE_TO_VERIFY,
                reason=REASON_SAFE_TO_VERIFY,
                current_state=current_state,
                now=now,
                recovery_ready=True,
                can_mark_verified=True,
            )
        return _decision(
            record,
            status=STOP_DUE_RECOVERY_REVIEW,
            reason=REASON_RECOVERY_REVIEW,
            current_state=current_state,
            now=now,
            recovery_ready=True,
        )

    if record.state != STOP_STATE_OWNED:
        return _decision(
            record,
            status=STOP_DUE_BLOCKED,
            reason=f"unsupported_stop_state:{record.state}",
            current_state=current_state,
            now=now,
            recovery_ready=True,
        )

    if now < due:
        return _decision(
            record,
            status=STOP_DUE_WAITING,
            reason=REASON_WAITING,
            current_state=current_state,
            now=now,
            recovery_ready=True,
        )
    if normalized == record.desired_state:
        return _decision(
            record,
            status=STOP_DUE_ALREADY_OFF,
            reason=REASON_ALREADY_OFF,
            current_state=current_state,
            now=now,
            recovery_ready=True,
            can_complete_noop=True,
        )
    if normalized == "on":
        return _decision(
            record,
            status=STOP_DUE_READY,
            reason=REASON_READY,
            current_state=current_state,
            now=now,
            recovery_ready=True,
            can_dispatch_stop=True,
        )
    return _decision(
        record,
        status=STOP_DUE_BLOCKED,
        reason="stop_entity_state_not_allowlisted",
        current_state=current_state,
        now=now,
        recovery_ready=True,
    )
