"""Read-only lifecycle recovery diagnostics for FRAKON Energy."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_lifecycle_recovery import (
    lifecycle_recovery_summary,
    recovery_diagnostic_for_record,
)
from .load_execution_lifecycle_runtime import lifecycle_repository

COMMAND_RECOVERY_DIAGNOSTICS = f"{DOMAIN}/load_execution_lifecycle/recovery"
_REGISTERED_KEY = "load_execution_lifecycle_recovery_websocket_registered"


async def async_lifecycle_recovery_diagnostics(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> dict[str, Any]:
    """Return startup recovery status and live entity evidence without mutation."""
    if not entry_id:
        raise ValueError("entry_id is required")
    summary = lifecycle_recovery_summary(hass, entry_id)
    records = await lifecycle_repository(hass, entry_id).async_list()
    diagnostics: list[dict[str, Any]] = []
    for record in records:
        state = hass.states.get(record.entity_id)
        current_state = str(state.state) if state is not None else None
        diagnostics.append(
            recovery_diagnostic_for_record(record, current_state=current_state)
        )
    return {
        "entry_id": entry_id,
        "recovery": summary.as_dict(),
        "lifecycles": diagnostics,
        "manual_review_required": any(
            item["diagnostic"] == "manual_recovery_review_required"
            for item in diagnostics
        ),
        "read_only": True,
        "state_transition_performed": False,
        "execution_performed": False,
        "executor_available": False,
    }


@callback
def async_register_load_execution_lifecycle_recovery_websocket(
    hass: HomeAssistant,
) -> None:
    """Register administrator-only read-only recovery diagnostics once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_RECOVERY_DIAGNOSTICS,
            vol.Required("entry_id"): str,
        }
    )
    async def websocket_recovery(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_lifecycle_recovery_diagnostics(
                hass,
                entry_id=msg["entry_id"],
            )
        except ValueError as err:
            connection.send_error(msg["id"], "execution_lifecycle_recovery_invalid", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_lifecycle_recovery_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_recovery)
    domain_data[_REGISTERED_KEY] = True
