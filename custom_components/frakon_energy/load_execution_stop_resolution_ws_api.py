"""Administrator-only stop no-op/verification WebSocket API."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_stop_lifecycle import StopLifecycleError
from .load_execution_stop_recovery import StopRecoveryBlockedError
from .load_execution_stop_resolution import (
    StopResolutionError,
    async_complete_stop_noop,
    async_verify_stop_resolution,
)

COMMAND_STOP_COMPLETE_NOOP = f"{DOMAIN}/load_execution_stop/complete_noop"
COMMAND_STOP_VERIFY = f"{DOMAIN}/load_execution_stop/verify"
_REGISTERED_KEY = "load_execution_stop_resolution_websocket_registered"


@callback
def async_register_load_execution_stop_resolution_websocket(hass: HomeAssistant) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    base_schema = {
        vol.Required("entry_id"): str,
        vol.Required("start_lifecycle_id"): str,
    }

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {vol.Required("type"): COMMAND_STOP_COMPLETE_NOOP, **base_schema}
    )
    async def websocket_complete_noop(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_complete_stop_noop(
                hass,
                entry_id=msg["entry_id"],
                start_lifecycle_id=msg["start_lifecycle_id"],
            )
        except StopRecoveryBlockedError as err:
            connection.send_error(msg["id"], "stop_recovery_blocked", str(err))
            return
        except (StopResolutionError, StopLifecycleError, ValueError) as err:
            connection.send_error(msg["id"], "stop_noop_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "stop_noop_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {vol.Required("type"): COMMAND_STOP_VERIFY, **base_schema}
    )
    async def websocket_verify(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_verify_stop_resolution(
                hass,
                entry_id=msg["entry_id"],
                start_lifecycle_id=msg["start_lifecycle_id"],
            )
        except StopRecoveryBlockedError as err:
            connection.send_error(msg["id"], "stop_recovery_blocked", str(err))
            return
        except (StopResolutionError, StopLifecycleError, ValueError) as err:
            connection.send_error(msg["id"], "stop_verify_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "stop_verify_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_complete_noop)
    websocket_api.async_register_command(hass, websocket_verify)
    domain_data[_REGISTERED_KEY] = True
