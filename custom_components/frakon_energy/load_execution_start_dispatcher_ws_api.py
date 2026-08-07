"""Administrator-only physical bounded-start WebSocket API."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_lifecycle import ExecutionLifecycleError
from .load_execution_lifecycle_recovery import LifecycleRecoveryBlockedError
from .load_execution_start_dispatcher import (
    StartDispatchError,
    StartDispatchUnknownOutcomeError,
    async_dispatch_bounded_start,
)
from .load_execution_stop_recovery import StopRecoveryBlockedError

COMMAND_START_DISPATCH = f"{DOMAIN}/load_execution_start/dispatch"
_REGISTERED_KEY = "load_execution_physical_start_dispatch_websocket_registered"


@callback
def async_register_load_execution_start_dispatcher_websocket(
    hass: HomeAssistant,
) -> None:
    """Register explicit administrator-only bounded start execution once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_START_DISPATCH,
            vol.Required("entry_id"): str,
            vol.Required("attempt_id"): str,
        }
    )
    async def websocket_dispatch_start(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_dispatch_bounded_start(
                hass,
                entry_id=msg["entry_id"],
                attempt_id=msg["attempt_id"],
                context=connection.context(msg),
            )
        except StartDispatchUnknownOutcomeError as err:
            connection.send_error(msg["id"], "start_dispatch_outcome_unknown", str(err))
            return
        except (LifecycleRecoveryBlockedError, StopRecoveryBlockedError) as err:
            connection.send_error(msg["id"], "start_dispatch_recovery_blocked", str(err))
            return
        except (StartDispatchError, ExecutionLifecycleError, ValueError) as err:
            connection.send_error(msg["id"], "start_dispatch_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "start_dispatch_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_dispatch_start)
    domain_data[_REGISTERED_KEY] = True
