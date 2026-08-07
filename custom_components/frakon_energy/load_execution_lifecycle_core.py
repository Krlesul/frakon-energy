"""Pure execution lifecycle state transitions for FRAKON Energy.

This module contains no persistence, WebSocket API, Home Assistant service call,
or executor. It only defines the allowed lifecycle states and transitions for a
future durable execution workflow.
"""

from __future__ import annotations

STATUS_PREPARED = "prepared"
STATUS_DISPATCHING = "dispatching"
STATUS_VERIFYING = "verifying"
STATUS_RECOVERY_REQUIRED = "recovery_required"
STATUS_VERIFIED = "verified"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

LIFECYCLE_STATUSES = frozenset(
    {
        STATUS_PREPARED,
        STATUS_DISPATCHING,
        STATUS_VERIFYING,
        STATUS_RECOVERY_REQUIRED,
        STATUS_VERIFIED,
        STATUS_FAILED,
        STATUS_CANCELLED,
    }
)

TERMINAL_STATUSES = frozenset(
    {
        STATUS_VERIFIED,
        STATUS_FAILED,
        STATUS_CANCELLED,
    }
)

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_PREPARED: frozenset({STATUS_DISPATCHING, STATUS_CANCELLED}),
    STATUS_DISPATCHING: frozenset({STATUS_VERIFYING, STATUS_RECOVERY_REQUIRED}),
    STATUS_VERIFYING: frozenset(
        {STATUS_VERIFIED, STATUS_FAILED, STATUS_RECOVERY_REQUIRED}
    ),
    STATUS_RECOVERY_REQUIRED: frozenset({STATUS_VERIFYING, STATUS_FAILED}),
    STATUS_VERIFIED: frozenset(),
    STATUS_FAILED: frozenset(),
    STATUS_CANCELLED: frozenset(),
}


class InvalidLifecycleTransition(ValueError):
    """Raised when an execution lifecycle transition is not allowed."""


def validate_status(status: str) -> str:
    """Validate and return one lifecycle status."""
    if status not in LIFECYCLE_STATUSES:
        raise ValueError(f"unsupported execution lifecycle status: {status}")
    return status


def is_terminal(status: str) -> bool:
    """Return whether a lifecycle status is terminal."""
    validate_status(status)
    return status in TERMINAL_STATUSES


def allowed_next_statuses(status: str) -> frozenset[str]:
    """Return the exact allowlisted next states for one lifecycle status."""
    validate_status(status)
    return _ALLOWED_TRANSITIONS[status]


def require_transition(current: str, target: str) -> None:
    """Reject any transition that is not explicitly allowlisted."""
    validate_status(current)
    validate_status(target)
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidLifecycleTransition(
            f"execution lifecycle transition is not allowed: {current} -> {target}"
        )
