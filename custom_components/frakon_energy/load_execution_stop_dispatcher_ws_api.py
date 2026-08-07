"""Administrator-only physical bounded-stop WebSocket API."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_stop_dispatcher import (
    StopDispatchError,
    StopDispatchUnknownOutcomeError,
    async_dispatch_due_stop,
)
from .load_execution_stop_lifecycle import StopLifecycleError
from .load_execution_stop_recovery import StopRecoveryBlockedError

COMMAND_STOP_DISPATCH = f"{DOMAIN}/load_execution_stop/dispatch"
_REGISTERED_KEY = "load_execution_physical_stop_dispatch_websocket_registered"


@callback
def async_register_load_execution_stop_dispatcher_websocket(
    hass: HomeAssistant,
) -> None:
    """Register the first physical executor endpoint once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_STOP_DISPATCH,
            vol.Required("entry_id"): str,
            vol.Required("start_lifecycle_id"): str,
        }
    )
    async def websocket_dispatch_stop(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_dispatch_due_stop(
                hass,
                entry_id=msg["entry_id"],
                start_lifecycle_id=msg["start_lifecycle_id"],
                context=connection.context(msg),
            )
        except StopDispatchUnknownOutcomeError as err:
            connection.send_error(msg["id"], "stop_dispatch_outcome_unknown", str(err))
            return
        except StopRecoveryBlockedError as err:
            connection.send_error(msg["id"], "stop_recovery_blocked", str(err))
            return
        except (StopDispatchError, StopLifecycleError, ValueError) as err:
            connection.send_error(msg["id"], "stop_dispatch_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "stop_dispatch_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_dispatch_stop)
    domain_data[_REGISTERED_KEY] = True
