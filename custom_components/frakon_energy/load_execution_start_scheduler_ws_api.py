"""Administrator-only read-only autonomous start scheduler status API."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_start_scheduler import start_scheduler

COMMAND_START_SCHEDULER = f"{DOMAIN}/load_execution_start/scheduler"
_REGISTERED_KEY = "load_execution_start_scheduler_websocket_registered"


async def async_start_scheduler_status(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> dict[str, Any]:
    if not entry_id:
        raise ValueError("entry_id is required")
    scheduler = start_scheduler(hass, entry_id)
    return {
        "entry_id": entry_id,
        "started": scheduler.started,
        "healthy": scheduler.healthy,
        "last_error": scheduler.last_error,
        "statuses": [item.as_dict() for item in scheduler.statuses()],
        "read_only": True,
        "creates_approval": False,
        "creates_attempt": False,
        "creates_lifecycle": False,
        "creates_stop_lease": False,
        "can_redispatch_unknown": False,
        "autonomous_start_enabled": scheduler.started and scheduler.healthy,
    }


@callback
def async_register_load_execution_start_scheduler_websocket(hass: HomeAssistant) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_START_SCHEDULER,
            vol.Required("entry_id"): str,
        }
    )
    async def websocket_start_scheduler_status(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_start_scheduler_status(
                hass,
                entry_id=msg["entry_id"],
            )
        except ValueError as err:
            connection.send_error(msg["id"], "execution_start_scheduler_invalid", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_start_scheduler_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_start_scheduler_status)
    domain_data[_REGISTERED_KEY] = True
