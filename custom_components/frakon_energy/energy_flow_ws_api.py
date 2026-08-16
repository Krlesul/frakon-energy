from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from .ws_auth import ensure_admin
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .energy_flow_model import build_energy_flow_snapshot

COMMAND_ENERGY_FLOW_STATUS = f"{DOMAIN}/energy_flow/status"
_REGISTERED_KEY = "energy_flow_status_websocket_registered"


def _entry(hass: HomeAssistant, entry_id: str):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ValueError("FRAKON Energy config entry not found")
    return entry


async def async_energy_flow_status(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> dict[str, Any]:
    """Return one authoritative read-only server-side energy-flow snapshot."""
    entry = _entry(hass, entry_id)
    return build_energy_flow_snapshot(
        hass,
        entry_id=entry_id,
        options=entry.options,
    ).as_dict()


@callback
def async_register_energy_flow_websocket(hass: HomeAssistant) -> None:
    """Register the administrator-only server-side energy-flow status command."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_ENERGY_FLOW_STATUS,
            vol.Required("entry_id"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_status(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        ensure_admin(connection)
        try:
            result = await async_energy_flow_status(
                hass,
                entry_id=msg["entry_id"],
            )
        except ValueError as err:
            connection.send_error(msg["id"], "energy_flow_status_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "energy_flow_status_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_status)
    domain_data[_REGISTERED_KEY] = True
