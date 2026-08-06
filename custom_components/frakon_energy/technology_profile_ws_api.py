from __future__ import annotations

from typing import Any, Mapping

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .technology_profile import HouseTechnology
from .technology_profile_storage import update_technology_enabled

COMMAND_SET_TECHNOLOGY_ENABLED = "frakon_energy/technology/set_enabled"
_REGISTERED_KEY = "technology_profile_websocket_registered"


@callback
def async_register_technology_profile_websocket(hass: HomeAssistant) -> None:
    """Register the administrator-only technology enable/disable command once."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_SET_TECHNOLOGY_ENABLED,
            vol.Required("entry_id"): str,
            vol.Required("technology"): vol.In([item.value for item in HouseTechnology]),
            vol.Required("enabled"): bool,
        }
    )
    @websocket_api.async_response
    async def websocket_set_enabled(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        connection.require_admin()
        entry = hass.config_entries.async_get_entry(str(msg["entry_id"]))
        if entry is None or entry.domain != DOMAIN:
            connection.send_error(
                msg["id"],
                "entry_not_found",
                "FRAKON Energy config entry was not found.",
            )
            return

        options = update_technology_enabled(
            entry.options,
            str(msg["technology"]),
            bool(msg["enabled"]),
        )
        hass.config_entries.async_update_entry(entry, options=options)
        connection.send_result(
            msg["id"],
            {
                "entry_id": entry.entry_id,
                "technology": str(msg["technology"]),
                "enabled": bool(msg["enabled"]),
            },
        )

    websocket_api.async_register_command(hass, websocket_set_enabled)
    domain_data[_REGISTERED_KEY] = True
