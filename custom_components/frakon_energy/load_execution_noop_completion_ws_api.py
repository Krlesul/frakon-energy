"""Administrator-only durable no-dispatch completion for FRAKON Energy."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_lifecycle_recovery import LifecycleRecoveryBlockedError
from .load_execution_noop_completion import (
    NoopCompletionError,
    async_complete_already_satisfied_noop,
)

COMMAND_COMPLETE_NOOP = f"{DOMAIN}/load_execution_lifecycle/complete_noop"
_REGISTERED_KEY = "load_execution_noop_completion_websocket_registered"


@callback
def async_register_load_execution_noop_completion_websocket(
    hass: HomeAssistant,
) -> None:
    """Register already-satisfied no-dispatch completion once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_COMPLETE_NOOP,
            vol.Required("entry_id"): str,
            vol.Required("attempt_id"): str,
        }
    )
    async def websocket_complete_noop(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_complete_already_satisfied_noop(
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
        except NoopCompletionError as err:
            connection.send_error(
                msg["id"],
                "execution_lifecycle_noop_rejected",
                str(err),
            )
            return
        except Exception as err:
            connection.send_error(
                msg["id"],
                "execution_lifecycle_noop_unavailable",
                str(err),
            )
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_complete_noop)
    domain_data[_REGISTERED_KEY] = True
