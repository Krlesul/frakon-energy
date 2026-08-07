"""Administrator-only read-only execution safety status API."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_safety_status import async_execution_safety_status

COMMAND_EXECUTION_SAFETY_STATUS = f"{DOMAIN}/load_execution/safety_status"
_REGISTERED_KEY = "load_execution_safety_status_websocket_registered"


@callback
def async_register_load_execution_safety_status_websocket(hass: HomeAssistant) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_EXECUTION_SAFETY_STATUS,
            vol.Required("entry_id"): str,
        }
    )
    async def websocket_safety_status(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_execution_safety_status(
                hass,
                entry_id=msg["entry_id"],
            )
        except ValueError as err:
            connection.send_error(msg["id"], "execution_safety_status_invalid", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_safety_status_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_safety_status)
    domain_data[_REGISTERED_KEY] = True
