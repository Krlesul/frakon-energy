"""Read-only aggregate execution safety status for FRAKON Energy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import time
from typing import Any

from homeassistant.core import HomeAssistant

from .load_execution_arm import async_execution_arm_status
from .load_execution_capacity_reservation import capacity_reservation_repository
from .load_execution_lifecycle import STATE_DISPATCHED, STATE_DISPATCHING, STATE_RECOVERY_REQUIRED, STATE_VERIFIED, ExecutionLifecycleRecord
from .load_execution_lifecycle_recovery import RECOVERY_OK, lifecycle_recovery_summary
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_execution_pending_run_scheduler import pending_run_scheduler
from .load_execution_start_scheduler import start_scheduler
from .load_execution_start_stop_ownership import async_start_stop_ownership_proof
from .load_execution_stop_lifecycle_runtime import stop_lifecycle_repository
from .load_execution_stop_recovery import STOP_RECOVERY_OK, stop_recovery_summary
from .load_execution_stop_scheduler import stop_scheduler
from .site_capacity import STATUS_OVER_LIMIT, STATUS_SOURCE_UNAVAILABLE, STATUS_TOPOLOGY_NOT_READY, build_site_capacity_status

_OWNERSHIP_REQUIRED_STATES = {STATE_DISPATCHING, STATE_DISPATCHED, STATE_RECOVERY_REQUIRED, STATE_VERIFIED}


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


async def _item(hass: HomeAssistant, *, entry_id: str, record: ExecutionLifecycleRecord, scheduler_status_by_start: dict[str, str]) -> ExecutionSafetyItem:
    proof = await async_start_stop_ownership_proof(hass, entry_id=entry_id, start=record)
    stop = await stop_lifecycle_repository(hass, entry_id).async_get_by_start_lifecycle_id(record.lifecycle_id)
    required = record.state in _OWNERSHIP_REQUIRED_STATES
    unsafe_reason = f"bounded_stop_ownership_not_ready:{proof.reason}" if required and not proof.ownership_ready else None
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
        safety_status="unsafe" if unsafe_reason else "safe",
        unsafe_reason=unsafe_reason,
    )


def _capacity_guard_summary(capacity: Any) -> dict[str, Any]:
    configured = bool(capacity.configured)
    guard_active = bool(capacity.execution_guard_active)
    data_ready = (
        not guard_active
        or (
            configured
            and capacity.topology_ready
            and capacity.source_available
            and capacity.status not in {STATUS_TOPOLOGY_NOT_READY, STATUS_SOURCE_UNAVAILABLE}
        )
    )
    current_limit_exceeded = guard_active and capacity.status == STATUS_OVER_LIMIT
    if not guard_active:
        blocking_reason = None
    elif not configured:
        blocking_reason = "site_capacity_limit_not_configured"
    elif not capacity.topology_ready or capacity.status == STATUS_TOPOLOGY_NOT_READY:
        blocking_reason = "site_capacity_topology_not_ready"
    elif not capacity.source_available or capacity.status == STATUS_SOURCE_UNAVAILABLE:
        blocking_reason = "site_capacity_source_unavailable"
    elif current_limit_exceeded:
        blocking_reason = "site_capacity_already_over_limit"
    else:
        blocking_reason = None
    return {
        "configured": configured,
        "guard_active": guard_active,
        "data_ready": data_ready,
        "currently_blocks_all_new_starts": blocking_reason is not None,
        "blocking_reason": blocking_reason,
        "status": capacity.status,
        "reason": capacity.reason,
        "max_grid_import_kw": capacity.max_grid_import_kw,
        "current_grid_import_kw": capacity.current_grid_import_kw,
        "grid_headroom_kw": capacity.grid_headroom_kw,
        "grid_over_limit_kw": capacity.grid_over_limit_kw,
        "utilization_percent": capacity.utilization_percent,
        "source_entity_id": capacity.source_entity_id,
        "plan_specific_headroom_check_required": guard_active and data_ready and not current_limit_exceeded,
        "read_only": True,
        "service_call_performed": False,
        "execution_performed": False,
    }


async def _capacity_reservation_summary(hass: HomeAssistant, *, entry_id: str) -> dict[str, Any]:
    now_ts = int(time.time())
    try:
        reservations = await capacity_reservation_repository(hass, entry_id).async_snapshot(now=now_ts)
    except Exception as err:
        return {
            "storage_healthy": False, "last_error": str(err), "active_count": None,
            "reserved_power_kw": None, "next_expiry_at": None, "reservations": [],
            "read_only": True, "state_transition_performed": False,
            "service_call_performed": False, "execution_performed": False,
        }
    total_kw = sum(item.power_kw for item in reservations)
    next_expiry = min((item.expires_at for item in reservations), default=None)
    return {
        "storage_healthy": True, "last_error": None, "active_count": len(reservations),
        "reserved_power_kw": total_kw, "next_expiry_at": next_expiry,
        "reservations": [item.as_dict() for item in reservations], "read_only": True,
        "state_transition_performed": False, "service_call_performed": False, "execution_performed": False,
    }


async def async_execution_safety_status(hass: HomeAssistant, *, entry_id: str) -> dict[str, Any]:
    if not entry_id:
        raise ValueError("entry_id is required")
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        raise ValueError(f"config entry not found: {entry_id}")
    capacity = build_site_capacity_status(hass, entry_id=entry_id, options=entry.options)
    capacity_guard = _capacity_guard_summary(capacity)
    capacity_reservations = await _capacity_reservation_summary(hass, entry_id=entry_id)

    start_recovery = lifecycle_recovery_summary(hass, entry_id)
    stop_recovery = stop_recovery_summary(hass, entry_id)
    stop_runtime = stop_scheduler(hass, entry_id)
    start_runtime = start_scheduler(hass, entry_id)
    pending_runtime = pending_run_scheduler(hass, entry_id)
    arm_status = await async_execution_arm_status(hass, entry_id)
    stop_scheduler_statuses = stop_runtime.statuses()
    start_scheduler_statuses = start_runtime.statuses()
    pending_scheduler_statuses = pending_runtime.statuses()
    scheduler_status_by_start = {status.start_lifecycle_id: status.status for status in stop_scheduler_statuses}
    records = await lifecycle_repository(hass, entry_id).async_list()
    items = [await _item(hass, entry_id=entry_id, record=record, scheduler_status_by_start=scheduler_status_by_start) for record in records]
    unsafe = [item.lifecycle_id for item in items if item.safety_status == "unsafe"]
    stop_runtime_ready = stop_recovery.status == STOP_RECOVERY_OK and stop_runtime.started and stop_runtime.healthy
    start_recovery_ready = start_recovery.status == RECOVERY_OK
    autonomous_start_runtime_ready = start_recovery_ready and stop_runtime_ready and start_runtime.started and start_runtime.healthy
    pending_run_runtime_ready = autonomous_start_runtime_ready and pending_runtime.started and pending_runtime.healthy
    execution_armed = bool(arm_status.get("storage_healthy") and arm_status.get("armed"))
    explicit_start_executor_available = start_recovery_ready and stop_runtime_ready and execution_armed
    return {
        "entry_id": entry_id,
        "execution_arm": arm_status,
        "site_capacity": capacity.as_dict(),
        "site_capacity_guard": capacity_guard,
        "site_capacity_reservations": capacity_reservations,
        "start_recovery": start_recovery.as_dict(),
        "stop_recovery": stop_recovery.as_dict(),
        "stop_scheduler": {"started": stop_runtime.started, "healthy": stop_runtime.healthy, "last_error": stop_runtime.last_error, "statuses": [status.as_dict() for status in stop_scheduler_statuses]},
        "start_scheduler": {"started": start_runtime.started, "healthy": start_runtime.healthy, "last_error": start_runtime.last_error, "statuses": [status.as_dict() for status in start_scheduler_statuses]},
        "pending_run_scheduler": {"started": pending_runtime.started, "healthy": pending_runtime.healthy, "last_error": pending_runtime.last_error, "statuses": [status.as_dict() for status in pending_scheduler_statuses], "calls_home_assistant_services_directly": False},
        "start_runtime_ready": start_recovery_ready,
        "stop_runtime_ready": stop_runtime_ready,
        "autonomous_start_runtime_ready": autonomous_start_runtime_ready,
        "pending_run_runtime_ready": pending_run_runtime_ready,
        "execution_armed": execution_armed,
        "explicit_start_executor_available": explicit_start_executor_available,
        "explicit_stop_executor_available": True,
        "autonomous_stop_enabled": stop_runtime_ready,
        "autonomous_start_enabled": autonomous_start_runtime_ready and execution_armed,
        "autonomous_pending_run_enabled": pending_run_runtime_ready,
        "unsafe_start_lifecycles": unsafe,
        "items": [item.as_dict() for item in items],
        "read_only": True,
        "state_transition_performed": False,
        "service_call_performed": False,
        "execution_performed": False,
    }
