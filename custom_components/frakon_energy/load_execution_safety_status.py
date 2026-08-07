"""Read-only aggregate execution safety status for FRAKON Energy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from homeassistant.core import HomeAssistant

from .load_execution_lifecycle import (
    STATE_DISPATCHED,
    STATE_DISPATCHING,
    STATE_RECOVERY_REQUIRED,
    STATE_VERIFIED,
    ExecutionLifecycleRecord,
)
from .load_execution_lifecycle_recovery import RECOVERY_OK, lifecycle_recovery_summary
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_execution_start_stop_ownership import async_start_stop_ownership_proof
from .load_execution_stop_lifecycle_runtime import stop_lifecycle_repository
from .load_execution_stop_recovery import STOP_RECOVERY_OK, stop_recovery_summary
from .load_execution_stop_scheduler import stop_scheduler

_OWNERSHIP_REQUIRED_STATES = {
    STATE_DISPATCHING,
    STATE_DISPATCHED,
    STATE_RECOVERY_REQUIRED,
    STATE_VERIFIED,
}


@dataclass(frozen=True, slots=True)
class ExecutionSafetyItem:
    attempt_id: str
    lifecycle_id: str
    lifecycle_state: str
    entity_id: str
    current_state: str | None
    service_call_performed: bool | None
    stop_ownership_required: bool
    stop_ownership_ready: bool
    stop_ownership_reason: str
    stop_lifecycle_state: str | None
    stop_scheduler_status: str | None
    safety_status: str
    unsafe_reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _live_state(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    return str(state.state).strip().lower() if state is not None else None


async def _item(
    hass: HomeAssistant,
    *,
    entry_id: str,
    record: ExecutionLifecycleRecord,
    scheduler_status_by_start: dict[str, str],
) -> ExecutionSafetyItem:
    proof = await async_start_stop_ownership_proof(
        hass,
        entry_id=entry_id,
        start=record,
    )
    stop = await stop_lifecycle_repository(
        hass,
        entry_id,
    ).async_get_by_start_lifecycle_id(record.lifecycle_id)
    required = record.state in _OWNERSHIP_REQUIRED_STATES
    unsafe_reason = None
    if required and not proof.ownership_ready:
        unsafe_reason = f"bounded_stop_ownership_not_ready:{proof.reason}"
    safety_status = "unsafe" if unsafe_reason else "safe"
    return ExecutionSafetyItem(
        attempt_id=record.attempt_id,
        lifecycle_id=record.lifecycle_id,
        lifecycle_state=record.state,
        entity_id=record.entity_id,
        current_state=_live_state(hass, record.entity_id),
        service_call_performed=record.as_dict()["service_call_performed"],
        stop_ownership_required=required,
        stop_ownership_ready=proof.ownership_ready,
        stop_ownership_reason=proof.reason,
        stop_lifecycle_state=stop.state if stop is not None else None,
        stop_scheduler_status=scheduler_status_by_start.get(record.lifecycle_id),
        safety_status=safety_status,
        unsafe_reason=unsafe_reason,
    )


async def async_execution_safety_status(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> dict[str, Any]:
    """Aggregate durable execution and stop-runtime safety without mutation."""
    if not entry_id:
        raise ValueError("entry_id is required")

    start_recovery = lifecycle_recovery_summary(hass, entry_id)
    stop_recovery = stop_recovery_summary(hass, entry_id)
    scheduler = stop_scheduler(hass, entry_id)
    scheduler_statuses = scheduler.statuses()
    scheduler_status_by_start = {
        status.start_lifecycle_id: status.status for status in scheduler_statuses
    }
    records = await lifecycle_repository(hass, entry_id).async_list()
    items = [
        await _item(
            hass,
            entry_id=entry_id,
            record=record,
            scheduler_status_by_start=scheduler_status_by_start,
        )
        for record in records
    ]
    unsafe = [item.lifecycle_id for item in items if item.safety_status == "unsafe"]
    stop_runtime_ready = (
        stop_recovery.status == STOP_RECOVERY_OK
        and scheduler.started
        and scheduler.healthy
    )
    start_runtime_ready = start_recovery.status == RECOVERY_OK
    return {
        "entry_id": entry_id,
        "start_recovery": start_recovery.as_dict(),
        "stop_recovery": stop_recovery.as_dict(),
        "stop_scheduler": {
            "started": scheduler.started,
            "healthy": scheduler.healthy,
            "last_error": scheduler.last_error,
            "statuses": [status.as_dict() for status in scheduler_statuses],
        },
        "start_runtime_ready": start_runtime_ready,
        "stop_runtime_ready": stop_runtime_ready,
        "explicit_start_executor_available": True,
        "explicit_stop_executor_available": True,
        "autonomous_stop_enabled": stop_runtime_ready,
        "autonomous_start_enabled": False,
        "unsafe_start_lifecycles": unsafe,
        "items": [item.as_dict() for item in items],
        "read_only": True,
        "state_transition_performed": False,
        "service_call_performed": False,
        "execution_performed": False,
    }
