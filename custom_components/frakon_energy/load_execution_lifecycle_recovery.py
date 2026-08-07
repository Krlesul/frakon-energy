"""Crash-safe startup recovery for durable FRAKON Energy execution lifecycles.

Startup recovery never performs a Home Assistant service call. Its only mutation
is converting an interrupted persisted ``dispatching`` record into the explicit
``recovery_required`` state so a future executor cannot guess the prior outcome.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_lifecycle import (
    STATE_DISPATCHED,
    STATE_DISPATCHING,
    STATE_RECOVERY_REQUIRED,
    ExecutionLifecycleRecord,
    require_recovery_after_restart,
)
from .load_execution_lifecycle_runtime import lifecycle_repository

_RECOVERY_STATUS_KEY = "load_execution_lifecycle_recovery_by_entry"
RECOVERY_OK = "ok"
RECOVERY_FAILED = "failed"
RECOVERY_NOT_INITIALIZED = "not_initialized"


class LifecycleRecoveryBlockedError(RuntimeError):
    """Raised when lifecycle mutation is blocked by startup recovery state."""


@dataclass(frozen=True, slots=True)
class LifecycleRecoverySummary:
    entry_id: str
    status: str
    scanned: int
    transitioned_to_recovery: int
    recovery_required: int
    dispatched_pending_verification: int
    error: str | None = None
    execution_performed: bool = False
    executor_available: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _status_map(hass: HomeAssistant) -> dict[str, LifecycleRecoverySummary]:
    domain_data = hass.data.setdefault(DOMAIN, {})
    value = domain_data.get(_RECOVERY_STATUS_KEY)
    if not isinstance(value, dict):
        value = {}
        domain_data[_RECOVERY_STATUS_KEY] = value
    return value


def lifecycle_recovery_summary(
    hass: HomeAssistant,
    entry_id: str,
) -> LifecycleRecoverySummary:
    """Return startup recovery state; absence is explicitly not initialized."""
    value = _status_map(hass).get(entry_id)
    if isinstance(value, LifecycleRecoverySummary):
        return value
    return LifecycleRecoverySummary(
        entry_id=entry_id,
        status=RECOVERY_NOT_INITIALIZED,
        scanned=0,
        transitioned_to_recovery=0,
        recovery_required=0,
        dispatched_pending_verification=0,
    )


def assert_lifecycle_recovery_ready(hass: HomeAssistant, entry_id: str) -> None:
    """Fail closed for lifecycle mutations until startup recovery succeeded."""
    summary = lifecycle_recovery_summary(hass, entry_id)
    if summary.status != RECOVERY_OK:
        detail = f": {summary.error}" if summary.error else ""
        raise LifecycleRecoveryBlockedError(
            f"execution lifecycle recovery is {summary.status}{detail}"
        )


def _count_states(records: tuple[ExecutionLifecycleRecord, ...]) -> tuple[int, int]:
    recovery_required = sum(record.state == STATE_RECOVERY_REQUIRED for record in records)
    dispatched = sum(record.state == STATE_DISPATCHED for record in records)
    return recovery_required, dispatched


async def async_initialize_lifecycle_recovery(
    hass: HomeAssistant,
    *,
    entry_id: str,
    now: datetime | None = None,
) -> LifecycleRecoverySummary:
    """Recover interrupted dispatches and remember whether execution is safe to mutate."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    repository = lifecycle_repository(hass, entry_id)
    try:
        records = await repository.async_list()
        transitioned = 0
        for record in records:
            if record.state != STATE_DISPATCHING:
                continue
            recovered = require_recovery_after_restart(
                record,
                now=max(int(current.timestamp()), record.updated_at),
            )
            await repository.async_update(recovered)
            transitioned += 1
        final_records = await repository.async_list()
        recovery_required, dispatched = _count_states(final_records)
        summary = LifecycleRecoverySummary(
            entry_id=entry_id,
            status=RECOVERY_OK,
            scanned=len(records),
            transitioned_to_recovery=transitioned,
            recovery_required=recovery_required,
            dispatched_pending_verification=dispatched,
        )
    except Exception as err:
        # Keep the energy integration itself available, but block lifecycle mutation.
        summary = LifecycleRecoverySummary(
            entry_id=entry_id,
            status=RECOVERY_FAILED,
            scanned=0,
            transitioned_to_recovery=0,
            recovery_required=0,
            dispatched_pending_verification=0,
            error=str(err),
        )
    _status_map(hass)[entry_id] = summary
    return summary


def recovery_diagnostic_for_record(
    record: ExecutionLifecycleRecord,
    *,
    current_state: str | None,
) -> dict[str, Any]:
    """Return read-only recovery evidence for one durable lifecycle record."""
    record.validated()
    normalized = current_state.strip().lower() if isinstance(current_state, str) else None
    desired_observed = normalized == record.desired_state
    if record.state == STATE_RECOVERY_REQUIRED:
        diagnostic = (
            "desired_state_observed_after_unknown_dispatch"
            if desired_observed
            else "manual_recovery_review_required"
        )
    elif record.state == STATE_DISPATCHED:
        diagnostic = (
            "desired_state_observed_pending_verification"
            if desired_observed
            else "dispatch_confirmed_but_desired_state_not_observed"
        )
    else:
        diagnostic = "no_dispatch_recovery_required"
    return {
        "lifecycle_id": record.lifecycle_id,
        "attempt_id": record.attempt_id,
        "state": record.state,
        "entity_id": record.entity_id,
        "current_state": normalized,
        "desired_state": record.desired_state,
        "desired_state_observed": desired_observed,
        "diagnostic": diagnostic,
        "service_call_status": record.service_call_status,
        "service_call_performed": record.as_dict()["service_call_performed"],
        "read_only": True,
        "execution_performed": False,
        "executor_available": False,
    }
