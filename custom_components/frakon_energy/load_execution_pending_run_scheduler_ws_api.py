"""Administrator-only read-only pending-run scheduler status API."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_pending_run_retention_runtime import pending_run_retention_status
from .load_execution_pending_run_scheduler import pending_run_scheduler

COMMAND_PENDING_RUN_SCHEDULER = f"{DOMAIN}/load_execution_pending_run/scheduler"
_REGISTERED_KEY = "load_execution_pending_run_scheduler_websocket_registered"


async def async_pending_run_scheduler_status(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> dict[str, Any]:
    if not entry_id:
        raise ValueError("entry_id is required")
    scheduler = pending_run_scheduler(hass, entry_id)
    retention = pending_run_retention_status(hass, entry_id)
    return {
        "entry_id": entry_id,
        "started": scheduler.started,
        "healthy": scheduler.healthy,
        "last_error": scheduler.last_error,
        "statuses": [status.as_dict() for status in scheduler.statuses()],
        "retention": retention.as_dict(),
        "creates_authority": False,
        "calls_home_assistant_services_directly": False,
        "delegates_only_to_existing_prepare_flows": True,
        "read_only": True,
        "state_transition_performed": False,
        "service_call_performed": False,
        "execution_performed": False,
    }


@callback
def async_register_load_execution_pending_run_scheduler_websocket(
    hass: HomeAssistant,
) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_PENDING_RUN_SCHEDULER,
            vol.Required("entry_id"): str,
        }
    )
    async def websocket_pending_run_scheduler(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_pending_run_scheduler_status(
                hass,
                entry_id=msg["entry_id"],
            )
        except ValueError as err:
            connection.send_error(msg["id"], "pending_run_scheduler_invalid", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "pending_run_scheduler_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_pending_run_scheduler)
    domain_data[_REGISTERED_KEY] = True
