"""Administrator-only guarded recovery verification for FRAKON Energy."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_lifecycle_recovery import LifecycleRecoveryBlockedError
from .load_execution_recovery_verification import (
    RecoveryVerificationError,
    async_verify_recovery_lifecycle,
)

COMMAND_RECOVERY_VERIFY = f"{DOMAIN}/load_execution_lifecycle/recovery_verify"
_REGISTERED_KEY = "load_execution_recovery_verification_websocket_registered"


@callback
def async_register_load_execution_recovery_verification_websocket(
    hass: HomeAssistant,
) -> None:
    """Register audit-only recovery verification once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_RECOVERY_VERIFY,
            vol.Required("entry_id"): str,
            vol.Required("attempt_id"): str,
        }
    )
    async def websocket_recovery_verify(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_verify_recovery_lifecycle(
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
        except RecoveryVerificationError as err:
            connection.send_error(
                msg["id"],
                "execution_lifecycle_recovery_verify_rejected",
                str(err),
            )
            return
        except Exception as err:
            connection.send_error(
                msg["id"],
                "execution_lifecycle_recovery_verify_unavailable",
                str(err),
            )
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_recovery_verify)
    domain_data[_REGISTERED_KEY] = True
