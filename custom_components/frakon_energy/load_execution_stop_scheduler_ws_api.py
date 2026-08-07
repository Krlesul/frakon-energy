"""Read-only runtime status API for the internal stop scheduler."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_stop_scheduler import stop_scheduler

COMMAND_STOP_SCHEDULER = f"{DOMAIN}/load_execution_stop/scheduler"
_REGISTERED_KEY = "load_execution_stop_scheduler_websocket_registered"


async def async_stop_scheduler_status(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> dict[str, Any]:
    if not entry_id:
        raise ValueError("entry_id is required")
    scheduler = stop_scheduler(hass, entry_id)
    statuses = scheduler.statuses()
    return {
        "entry_id": entry_id,
        "started": scheduler.started,
        "healthy": scheduler.healthy,
        "last_error": scheduler.last_error,
        "statuses": [item.as_dict() for item in statuses],
        "ready_to_stop": [
            item.start_lifecycle_id for item in statuses if item.dispatch_required
        ],
        "read_only": True,
        "state_transition_performed": False,
        "service_call_performed": False,
        "execution_performed": False,
        "executor_available": False,
    }


@callback
def async_register_load_execution_stop_scheduler_websocket(
    hass: HomeAssistant,
) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_STOP_SCHEDULER,
            vol.Required("entry_id"): str,
        }
    )
    async def websocket_status(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_stop_scheduler_status(
                hass,
                entry_id=msg["entry_id"],
            )
        except ValueError as err:
            connection.send_error(msg["id"], "stop_scheduler_invalid", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "stop_scheduler_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_status)
    domain_data[_REGISTERED_KEY] = True
