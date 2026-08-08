"""Administrator-only final bounded dispatch gate for FRAKON Energy."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_bounded_dispatch_gate import (
    BOUNDED_GATE_BLOCKED,
    BoundedDispatchDecision,
    evaluate_bounded_dispatch_gate,
)
from .load_execution_dispatch_gate import DispatchGateDecision
from .load_execution_dispatch_gate_ws_api import (
    DispatchGateLookupError,
    async_execution_dispatch_gate,
)
from .load_execution_lifecycle import ExecutionLifecycleRecord
from .load_execution_lifecycle_recovery import LifecycleRecoveryBlockedError
from .load_execution_site_capacity_gate import evaluate_site_capacity_execution_gate
from .load_execution_stop_lease_runtime import stop_lease_repository
from .site_capacity import build_site_capacity_status

COMMAND_BOUNDED_DISPATCH_GATE = f"{DOMAIN}/load_execution_lifecycle/bounded_dispatch_gate"
_REGISTERED_KEY = "load_execution_bounded_dispatch_gate_websocket_registered"


class BoundedDispatchGateError(ValueError):
    """Raised when bounded dispatch evidence cannot be reconstructed."""


async def async_bounded_dispatch_gate(
    hass: HomeAssistant,
    *,
    entry_id: str,
    attempt_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Require dispatch evidence, stop lease and configured site-capacity safety."""
    if not entry_id or not attempt_id:
        raise BoundedDispatchGateError("entry_id and attempt_id are required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise BoundedDispatchGateError("now must be timezone-aware")

    gate_payload = await async_execution_dispatch_gate(
        hass,
        entry_id=entry_id,
        attempt_id=attempt_id,
        now=current,
    )
    lifecycle_value = gate_payload.get("lifecycle")
    gate_value = gate_payload.get("dispatch_gate")
    if not isinstance(lifecycle_value, dict) or not isinstance(gate_value, dict):
        raise BoundedDispatchGateError("dispatch gate audit evidence is invalid")
    try:
        lifecycle = ExecutionLifecycleRecord.from_dict(lifecycle_value)
        dispatch_gate = DispatchGateDecision(**gate_value)
    except (TypeError, ValueError) as err:
        raise BoundedDispatchGateError("dispatch gate audit evidence is invalid") from err

    lease = await stop_lease_repository(hass, entry_id).async_get_by_lifecycle_id(
        lifecycle.lifecycle_id
    )
    decision: BoundedDispatchDecision = evaluate_bounded_dispatch_gate(
        lifecycle=lifecycle,
        dispatch_gate=dispatch_gate,
        stop_lease=lease,
    )

    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        raise BoundedDispatchGateError(f"config entry not found: {entry_id}")
    capacity = build_site_capacity_status(
        hass,
        entry_id=entry_id,
        options=entry.options,
    )
    capacity_gate = evaluate_site_capacity_execution_gate(
        capacity=capacity,
        planned_power_kw=lifecycle.plan.power_kw,
    )
    if decision.can_start and not capacity_gate.can_start:
        decision = replace(
            decision,
            status=BOUNDED_GATE_BLOCKED,
            reason=capacity_gate.reason,
            can_start=False,
        )

    return {
        "entry_id": entry_id,
        "lifecycle": lifecycle.as_dict(),
        "dispatch_gate": dispatch_gate.as_dict(),
        "stop_lease": lease.as_dict() if lease is not None else None,
        "site_capacity": capacity.as_dict(),
        "site_capacity_gate": capacity_gate.as_dict(),
        "bounded_dispatch_gate": decision.as_dict(),
        "read_only": True,
        "state_transition_performed": False,
        "service_call_performed": False,
        "execution_performed": False,
        "executor_available": False,
    }


@callback
def async_register_load_execution_bounded_dispatch_gate_websocket(
    hass: HomeAssistant,
) -> None:
    """Register the administrator-only final bounded dispatch gate once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_BOUNDED_DISPATCH_GATE,
            vol.Required("entry_id"): str,
            vol.Required("attempt_id"): str,
        }
    )
    async def websocket_bounded_dispatch_gate(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_bounded_dispatch_gate(
                hass,
                entry_id=msg["entry_id"],
                attempt_id=msg["attempt_id"],
            )
        except LifecycleRecoveryBlockedError as err:
            connection.send_error(
                msg["id"],
                "execution_lifecycle_recovery_blocked",
                str(err),
            )
            return
        except (BoundedDispatchGateError, DispatchGateLookupError, ValueError) as err:
            connection.send_error(
                msg["id"],
                "execution_bounded_dispatch_gate_rejected",
                str(err),
            )
            return
        except Exception as err:
            connection.send_error(
                msg["id"],
                "execution_bounded_dispatch_gate_unavailable",
                str(err),
            )
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_bounded_dispatch_gate)
    domain_data[_REGISTERED_KEY] = True
