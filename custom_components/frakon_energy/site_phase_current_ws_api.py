from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .site_phase_current import build_site_phase_current_status

COMMAND_SITE_PHASE_CURRENT_STATUS = f"{DOMAIN}/site_phase_current/status"
_REGISTERED_KEY = "site_phase_current_websocket_registered"


def _entry(hass: HomeAssistant, entry_id: str):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ValueError("FRAKON Energy config entry not found")
    return entry


async def async_site_phase_current_status(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> dict[str, Any]:
    entry = _entry(hass, entry_id)
    return build_site_phase_current_status(
        hass,
        entry_id=entry_id,
        options=entry.options,
    ).as_dict()


@callback
def async_register_site_phase_current_websocket(hass: HomeAssistant) -> None:
    """Register admin-only read-only L1/L2/L3 current diagnostics."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_SITE_PHASE_CURRENT_STATUS,
            vol.Required("entry_id"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_status(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        connection.require_admin()
        try:
            result = await async_site_phase_current_status(hass, entry_id=msg["entry_id"])
        except ValueError as err:
            connection.send_error(msg["id"], "site_phase_current_status_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "site_phase_current_status_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_status)
    domain_data[_REGISTERED_KEY] = True