"""Read-only runtime status API for the FRAKON Energy prepare-only scheduler."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_lifecycle_recovery import lifecycle_recovery_summary
from .load_execution_prepare_scheduler import existing_prepare_scheduler

COMMAND_PREPARE_SCHEDULER_STATUS = f"{DOMAIN}/load_execution_schedule/scheduler"
_REGISTERED_KEY = "load_execution_prepare_scheduler_websocket_registered"


async def async_prepare_scheduler_status(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> dict[str, Any]:
    """Return timer/runtime state without refreshing or mutating the scheduler."""
    if not entry_id:
        raise ValueError("entry_id is required")
    scheduler = existing_prepare_scheduler(hass, entry_id)
    recovery = lifecycle_recovery_summary(hass, entry_id)
    return {
        "entry_id": entry_id,
        "started": scheduler.started if scheduler is not None else False,
        "statuses": [item.as_dict() for item in scheduler.statuses()] if scheduler is not None else [],
        "recovery": recovery.as_dict(),
        "prepare_only": True,
        "read_only": True,
        "state_transition_performed": False,
        "execution_performed": False,
        "service_call_performed": False,
        "executor_available": False,
    }


@callback
def async_register_load_execution_prepare_scheduler_websocket(
    hass: HomeAssistant,
) -> None:
    """Register administrator-only read-only scheduler status once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_PREPARE_SCHEDULER_STATUS,
            vol.Required("entry_id"): str,
        }
    )
    async def websocket_status(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_prepare_scheduler_status(hass, entry_id=msg["entry_id"])
        except ValueError as err:
            connection.send_error(msg["id"], "execution_prepare_scheduler_status_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_prepare_scheduler_status_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_status)
    domain_data[_REGISTERED_KEY] = True
