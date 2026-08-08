"""Administrator-only read-only execution commissioning preflight API."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_bounded_dispatch_gate_ws_api import (
    BoundedDispatchGateError,
    DispatchGateLookupError,
)
from .load_execution_commissioning_preflight import (
    ExecutionCommissioningPreflightError,
    async_execution_commissioning_preflight,
)
from .load_execution_lifecycle_recovery import LifecycleRecoveryBlockedError

COMMAND_EXECUTION_COMMISSIONING_PREFLIGHT = (
    f"{DOMAIN}/load_execution/commissioning_preflight"
)
_REGISTERED_KEY = "load_execution_commissioning_preflight_websocket_registered"


@callback
def async_register_load_execution_commissioning_preflight_websocket(
    hass: HomeAssistant,
) -> None:
    """Register the admin-only read-only commissioning preflight once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_EXECUTION_COMMISSIONING_PREFLIGHT,
            vol.Required("entry_id"): str,
            vol.Required("attempt_id"): str,
        }
    )
    async def websocket_execution_commissioning_preflight(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_execution_commissioning_preflight(
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
        except (
            ExecutionCommissioningPreflightError,
            BoundedDispatchGateError,
            DispatchGateLookupError,
            ValueError,
        ) as err:
            connection.send_error(
                msg["id"],
                "execution_commissioning_preflight_rejected",
                str(err),
            )
            return
        except Exception as err:
            connection.send_error(
                msg["id"],
                "execution_commissioning_preflight_unavailable",
                str(err),
            )
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(
        hass,
        websocket_execution_commissioning_preflight,
    )
    domain_data[_REGISTERED_KEY] = True
