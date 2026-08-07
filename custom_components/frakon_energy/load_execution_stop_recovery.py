"""Fail-closed startup recovery for durable stop execution lifecycles."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_stop_lifecycle import (
    STOP_STATE_DISPATCHED,
    STOP_STATE_DISPATCHING,
    STOP_STATE_RECOVERY_REQUIRED,
    ExecutionStopLifecycleRecord,
    require_stop_recovery_after_restart,
)
from .load_execution_stop_lifecycle_runtime import stop_lifecycle_repository

_RECOVERY_STATUS_KEY = "load_execution_stop_recovery_by_entry"
STOP_RECOVERY_OK = "ok"
STOP_RECOVERY_FAILED = "failed"
STOP_RECOVERY_NOT_INITIALIZED = "not_initialized"


class StopRecoveryBlockedError(RuntimeError):
    """Raised when stop lifecycle mutation is blocked by startup recovery."""


@dataclass(frozen=True, slots=True)
class StopRecoverySummary:
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


def _status_map(hass: HomeAssistant) -> dict[str, StopRecoverySummary]:
    domain_data = hass.data.setdefault(DOMAIN, {})
    value = domain_data.get(_RECOVERY_STATUS_KEY)
    if not isinstance(value, dict):
        value = {}
        domain_data[_RECOVERY_STATUS_KEY] = value
    return value


def stop_recovery_summary(hass: HomeAssistant, entry_id: str) -> StopRecoverySummary:
    value = _status_map(hass).get(entry_id)
    if isinstance(value, StopRecoverySummary):
        return value
    return StopRecoverySummary(
        entry_id=entry_id,
        status=STOP_RECOVERY_NOT_INITIALIZED,
        scanned=0,
        transitioned_to_recovery=0,
        recovery_required=0,
        dispatched_pending_verification=0,
    )


def assert_stop_recovery_ready(hass: HomeAssistant, entry_id: str) -> None:
    summary = stop_recovery_summary(hass, entry_id)
    if summary.status != STOP_RECOVERY_OK:
        detail = f": {summary.error}" if summary.error else ""
        raise StopRecoveryBlockedError(
            f"stop lifecycle recovery is {summary.status}{detail}"
        )


def _count_states(records: tuple[ExecutionStopLifecycleRecord, ...]) -> tuple[int, int]:
    recovery_required = sum(
        record.state == STOP_STATE_RECOVERY_REQUIRED for record in records
    )
    dispatched = sum(record.state == STOP_STATE_DISPATCHED for record in records)
    return recovery_required, dispatched


async def async_initialize_stop_recovery(
    hass: HomeAssistant,
    *,
    entry_id: str,
    now: datetime | None = None,
) -> StopRecoverySummary:
    """Recover interrupted stop dispatches without performing any service call."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    repository = stop_lifecycle_repository(hass, entry_id)
    try:
        records = await repository.async_list()
        transitioned = 0
        for record in records:
            if record.state != STOP_STATE_DISPATCHING:
                continue
            recovered = require_stop_recovery_after_restart(
                record,
                now=max(int(current.timestamp()), record.updated_at),
            )
            await repository.async_update(recovered)
            transitioned += 1
        final_records = await repository.async_list()
        recovery_required, dispatched = _count_states(final_records)
        summary = StopRecoverySummary(
            entry_id=entry_id,
            status=STOP_RECOVERY_OK,
            scanned=len(records),
            transitioned_to_recovery=transitioned,
            recovery_required=recovery_required,
            dispatched_pending_verification=dispatched,
        )
    except Exception as err:
        summary = StopRecoverySummary(
            entry_id=entry_id,
            status=STOP_RECOVERY_FAILED,
            scanned=0,
            transitioned_to_recovery=0,
            recovery_required=0,
            dispatched_pending_verification=0,
            error=str(err),
        )
    _status_map(hass)[entry_id] = summary
    return summary
