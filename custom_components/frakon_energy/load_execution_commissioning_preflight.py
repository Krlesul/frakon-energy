"""Read-only commissioning preflight for bounded FRAKON Energy execution.

The preflight is designed for field commissioning while the global execution
interlock is DISARMED. It reconstructs the exact durable start/stop intent and
all runtime safety prerequisites without creating authority, mutating lifecycle
state, reserving a future decision, or calling a Home Assistant service.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from .load_execution_arm import execution_arm_guard
from .load_execution_bounded_dispatch_gate import (
    BOUNDED_GATE_ALREADY_SATISFIED,
    BOUNDED_GATE_READY,
)
from .load_execution_bounded_dispatch_gate_ws_api import async_bounded_dispatch_gate
from .load_execution_lifecycle_recovery import RECOVERY_OK
from .load_execution_safety_status import async_execution_safety_status
from .load_execution_stop_recovery import STOP_RECOVERY_OK

PREFLIGHT_READY_FOR_ARM = "ready_for_arm"
PREFLIGHT_BLOCKED = "blocked"
PREFLIGHT_NO_START_NEEDED = "no_start_needed"
PREFLIGHT_ALREADY_ARMED = "already_armed"

COMMISSIONING_TARGET_HELPER = "home_assistant_helper"
COMMISSIONING_TARGET_PHYSICAL_CAPABLE = "physical_capable_target"


class ExecutionCommissioningPreflightError(ValueError):
    """Raised when commissioning evidence cannot be reconstructed safely."""


def _required_dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExecutionCommissioningPreflightError(f"{name} evidence is invalid")
    return value


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ExecutionCommissioningPreflightError(f"{name} evidence is invalid")
    return value


def _commissioning_target(start_domain: str, entity_id: str) -> dict[str, Any]:
    """Classify direct service reach without inferring downstream automation safety."""
    helper = start_domain == "input_boolean" and entity_id.startswith("input_boolean.")
    return {
        "class": (
            COMMISSIONING_TARGET_HELPER
            if helper
            else COMMISSIONING_TARGET_PHYSICAL_CAPABLE
        ),
        "direct_hardware_service": not helper,
        "home_assistant_helper": helper,
        "indirect_automation_side_effects_assessed": False,
        "recommended_first_field_test_target": helper,
        "requires_downstream_automation_review": helper,
    }


async def async_execution_commissioning_preflight(
    hass: HomeAssistant,
    *,
    entry_id: str,
    attempt_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Audit whether one durable attempt would be eligible after a future ARM.

    The shared ARM guard freezes the commissioning interlock while evidence is
    collected. The result remains advisory only: ARM never trusts this snapshot
    and the real dispatcher reruns every authoritative gate at execution time.
    """
    if not entry_id or not attempt_id:
        raise ExecutionCommissioningPreflightError(
            "entry_id and attempt_id are required"
        )
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ExecutionCommissioningPreflightError("now must be timezone-aware")

    async with execution_arm_guard(hass, entry_id):
        safety = await async_execution_safety_status(hass, entry_id=entry_id)
        gate = await async_bounded_dispatch_gate(
            hass,
            entry_id=entry_id,
            attempt_id=attempt_id,
            now=current,
        )

        arm = _required_dict(safety.get("execution_arm"), "execution arm")
        start_recovery = _required_dict(safety.get("start_recovery"), "start recovery")
        stop_recovery = _required_dict(safety.get("stop_recovery"), "stop recovery")
        start_scheduler = _required_dict(safety.get("start_scheduler"), "start scheduler")
        stop_scheduler = _required_dict(safety.get("stop_scheduler"), "stop scheduler")
        lifecycle = _required_dict(gate.get("lifecycle"), "lifecycle")
        decision = _required_dict(
            gate.get("bounded_dispatch_gate"), "bounded dispatch gate"
        )
        stop_lease_value = gate.get("stop_lease")
        if stop_lease_value is not None and not isinstance(stop_lease_value, dict):
            raise ExecutionCommissioningPreflightError("stop lease evidence is invalid")
        stop_lease = stop_lease_value if isinstance(stop_lease_value, dict) else None

        entity_id = _required_text(lifecycle.get("entity_id"), "entity_id")
        start_domain = _required_text(
            lifecycle.get("service_domain"), "start service domain"
        )
        start_service = _required_text(
            lifecycle.get("service_name"), "start service name"
        )
        gate_status = _required_text(decision.get("status"), "bounded gate status")
        gate_reason = _required_text(decision.get("reason"), "bounded gate reason")
        target_classification = _commissioning_target(start_domain, entity_id)

        arm_storage_healthy = arm.get("storage_healthy") is True
        execution_armed = arm.get("armed") is True and arm_storage_healthy
        start_recovery_ready = start_recovery.get("status") == RECOVERY_OK
        stop_recovery_ready = stop_recovery.get("status") == STOP_RECOVERY_OK
        start_scheduler_ready = (
            start_scheduler.get("started") is True
            and start_scheduler.get("healthy") is True
        )
        stop_scheduler_ready = (
            stop_scheduler.get("started") is True
            and stop_scheduler.get("healthy") is True
        )
        bounded_gate_ready = gate_status == BOUNDED_GATE_READY
        already_satisfied = gate_status == BOUNDED_GATE_ALREADY_SATISFIED
        stop_lease_present = stop_lease is not None
        stop_lease_matches = decision.get("stop_lease_matches") is True
        dispatch_gate_matches = decision.get("dispatch_gate_matches") is True

        reasons: list[str] = []
        if not arm_storage_healthy:
            reasons.append("execution_arm_storage_unhealthy")
        elif execution_armed:
            reasons.append("execution_is_armed_commissioning_requires_disarmed")
        if not start_recovery_ready:
            reasons.append(f"start_recovery_{start_recovery.get('status')}")
        if not stop_recovery_ready:
            reasons.append(f"stop_recovery_{stop_recovery.get('status')}")
        if not start_scheduler_ready:
            reasons.append("autonomous_start_scheduler_not_ready")
        if not stop_scheduler_ready:
            reasons.append("autonomous_stop_scheduler_not_ready")
        if not bounded_gate_ready and not already_satisfied:
            reasons.append(f"bounded_gate_{gate_status}:{gate_reason}")
        if bounded_gate_ready and not stop_lease_present:
            reasons.append("durable_stop_lease_missing")
        if bounded_gate_ready and not stop_lease_matches:
            reasons.append("durable_stop_lease_mismatch")
        if bounded_gate_ready and not dispatch_gate_matches:
            reasons.append("dispatch_gate_mismatch")

        commissioning_window_safe = arm_storage_healthy and not execution_armed
        can_arm_to_execute = (
            commissioning_window_safe
            and start_recovery_ready
            and stop_recovery_ready
            and start_scheduler_ready
            and stop_scheduler_ready
            and bounded_gate_ready
            and stop_lease_present
            and stop_lease_matches
            and dispatch_gate_matches
        )

        if execution_armed:
            preflight_status = PREFLIGHT_ALREADY_ARMED
        elif already_satisfied and commissioning_window_safe:
            preflight_status = PREFLIGHT_NO_START_NEEDED
        elif can_arm_to_execute:
            preflight_status = PREFLIGHT_READY_FOR_ARM
        else:
            preflight_status = PREFLIGHT_BLOCKED

        stop_action: dict[str, Any] | None = None
        if stop_lease is not None:
            stop_action = {
                "service_domain": _required_text(
                    stop_lease.get("service_domain"), "stop service domain"
                ),
                "service_name": _required_text(
                    stop_lease.get("service_name"), "stop service name"
                ),
                "entity_id": _required_text(
                    stop_lease.get("entity_id"), "stop entity_id"
                ),
                "service_data": {},
                "ends_at": _required_text(stop_lease.get("ends_at"), "stop ends_at"),
            }

        return {
            "entry_id": entry_id,
            "attempt_id": attempt_id,
            "status": preflight_status,
            "reasons": reasons,
            "commissioning_window_safe": commissioning_window_safe,
            "can_arm_to_execute": can_arm_to_execute,
            "arm_is_only_remaining_interlock": can_arm_to_execute,
            "execution_arm": arm,
            "runtime": {
                "start_recovery_ready": start_recovery_ready,
                "stop_recovery_ready": stop_recovery_ready,
                "start_scheduler_ready": start_scheduler_ready,
                "stop_scheduler_ready": stop_scheduler_ready,
            },
            "bounded_dispatch_gate": decision,
            "commissioning_target": target_classification,
            "immutable_start_action": {
                "service_domain": start_domain,
                "service_name": start_service,
                "entity_id": entity_id,
                "service_data": {},
            },
            "immutable_stop_action": stop_action,
            "durable_stop_lease_present": stop_lease_present,
            "durable_stop_lease_matches": stop_lease_matches,
            "client_supplied_action_fields": False,
            "preflight_snapshot_reserves_execution": False,
            "gates_rechecked_after_arm": True,
            "dry_run": True,
            "read_only": True,
            "state_transition_performed": False,
            "service_call_performed": False,
            "execution_performed": False,
        }
