"""WebSocket API for FRAKON Energy dashboard visibility settings."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .dashboard_display_settings import DashboardDisplaySettings

COMMAND_GET = f"{DOMAIN}/dashboard_display_settings/get"
COMMAND_SET = f"{DOMAIN}/dashboard_display_settings/set"
_REGISTERED_KEY = "dashboard_display_settings_websocket_registered"


def _entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ValueError("FRAKON Energy config entry not found")
    return entry


@callback
def async_register_dashboard_display_settings_websocket(hass: HomeAssistant) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command({
        vol.Required("type"): COMMAND_GET,
        vol.Required("entry_id"): str,
    })
    @websocket_api.async_response
    async def websocket_get(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            settings = DashboardDisplaySettings.from_options(_entry(hass, msg["entry_id"]).options)
        except (ValueError, TypeError) as err:
            connection.send_error(msg["id"], "invalid_dashboard_display_settings", str(err))
            return
        connection.send_result(msg["id"], settings.as_dict())

    @websocket_api.websocket_command({
        vol.Required("type"): COMMAND_SET,
        vol.Required("entry_id"): str,
        vol.Required("key"): vol.In(DashboardDisplaySettings.keys()),
        vol.Required("enabled"): bool,
    })
    @websocket_api.async_response
    async def websocket_set(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            entry = _entry(hass, msg["entry_id"])
            current = DashboardDisplaySettings.from_options(entry.options)
            settings = current.with_value(msg["key"], msg["enabled"])
        except (ValueError, TypeError) as err:
            connection.send_error(msg["id"], "invalid_dashboard_display_settings", str(err))
            return
        options = dict(entry.options)
        options.update(settings.option_values())
        hass.config_entries.async_update_entry(entry, options=options)
        connection.send_result(msg["id"], settings.as_dict())

    websocket_api.async_register_command(hass, websocket_get)
    websocket_api.async_register_command(hass, websocket_set)
    domain_data[_REGISTERED_KEY] = True
