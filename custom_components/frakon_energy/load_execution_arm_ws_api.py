"""Administrator-only execution ARM/DISARM WebSocket API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_arm import (
    EXECUTION_ARM_CONFIRMATION,
    ExecutionArmError,
    async_execution_arm_status,
    async_set_execution_armed,
)
from .load_execution_start_scheduler import async_refresh_start_scheduler_if_started

COMMAND_EXECUTION_ARM_STATUS = f"{DOMAIN}/load_execution/arm_status"
COMMAND_EXECUTION_ARM = f"{DOMAIN}/load_execution/arm"
COMMAND_EXECUTION_DISARM = f"{DOMAIN}/load_execution/disarm"
_REGISTERED_KEY = "load_execution_arm_websocket_registered"


def _entry_exists(hass: HomeAssistant, entry_id: str) -> None:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ValueError("FRAKON Energy config entry not found")


def _actor(connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> str | None:
    context = connection.context(msg)
    return context.user_id


async def _set_and_refresh(
    hass: HomeAssistant,
    *,
    entry_id: str,
    armed: bool,
    changed_by: str | None,
) -> dict[str, Any]:
    result = await async_set_execution_armed(
        hass,
        entry_id=entry_id,
        armed=armed,
        changed_at=int(datetime.now(timezone.utc).timestamp()),
        changed_by=changed_by,
    )
    # Refreshing while DISARMED is inert. Refreshing immediately after an explicit
    # ARM lets already-approved/prepared work continue through all existing gates.
    await async_refresh_start_scheduler_if_started(hass, entry_id)
    status = await async_execution_arm_status(hass, entry_id)
    return {
        **result.as_dict(),
        "status": status,
        "new_physical_starts_allowed": bool(
            status.get("armed") and status.get("storage_healthy")
        ),
        "stop_obligations_remain_enabled": True,
    }


@callback
def async_register_load_execution_arm_websocket(hass: HomeAssistant) -> None:
    """Register persistent global start-execution interlock controls once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_EXECUTION_ARM_STATUS,
            vol.Required("entry_id"): str,
        }
    )
    async def websocket_arm_status(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            _entry_exists(hass, msg["entry_id"])
            result = await async_execution_arm_status(hass, msg["entry_id"])
        except ValueError as err:
            connection.send_error(msg["id"], "execution_arm_invalid", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_arm_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_EXECUTION_ARM,
            vol.Required("entry_id"): str,
            vol.Required("confirmation"): vol.In((EXECUTION_ARM_CONFIRMATION,)),
        }
    )
    async def websocket_arm(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            _entry_exists(hass, msg["entry_id"])
            result = await _set_and_refresh(
                hass,
                entry_id=msg["entry_id"],
                armed=True,
                changed_by=_actor(connection, msg),
            )
        except (ExecutionArmError, ValueError) as err:
            connection.send_error(msg["id"], "execution_arm_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_arm_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_EXECUTION_DISARM,
            vol.Required("entry_id"): str,
        }
    )
    async def websocket_disarm(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            _entry_exists(hass, msg["entry_id"])
            result = await _set_and_refresh(
                hass,
                entry_id=msg["entry_id"],
                armed=False,
                changed_by=_actor(connection, msg),
            )
        except (ExecutionArmError, ValueError) as err:
            connection.send_error(msg["id"], "execution_disarm_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_disarm_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_arm_status)
    websocket_api.async_register_command(hass, websocket_arm)
    websocket_api.async_register_command(hass, websocket_disarm)
    domain_data[_REGISTERED_KEY] = True
