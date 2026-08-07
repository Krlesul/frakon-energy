from __future__ import annotations

from typing import Any, Mapping

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .technology_profile import HouseTechnology
from .technology_profile_storage import update_technology_enabled

COMMAND_SET_TECHNOLOGY_ENABLED = "frakon_energy/technology/set_enabled"
COMMAND_GET_ENERGY_FLOW_SETTINGS = "frakon_energy/energy_flow/get"
COMMAND_SET_ENERGY_FLOW_SETTINGS = "frakon_energy/energy_flow/set"
CONF_ENERGY_FLOW = "energy_flow"
CONF_BATTERY_POWER_SIGN = "battery_power_sign"
BATTERY_POWER_SIGNS = ("unknown", "positive_is_charge", "positive_is_discharge")
_REGISTERED_KEY = "technology_profile_websocket_registered"


def _entry_or_error(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: Mapping[str, Any],
):
    entry = hass.config_entries.async_get_entry(str(msg["entry_id"]))
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(
            msg["id"],
            "entry_not_found",
            "FRAKON Energy config entry was not found.",
        )
        return None
    return entry


def _flow_settings(options: Mapping[str, Any]) -> dict[str, str]:
    raw = options.get(CONF_ENERGY_FLOW, {})
    stored = raw if isinstance(raw, Mapping) else {}
    sign = str(stored.get(CONF_BATTERY_POWER_SIGN, "unknown"))
    if sign not in BATTERY_POWER_SIGNS:
        sign = "unknown"
    return {CONF_BATTERY_POWER_SIGN: sign}


@callback
def async_register_technology_profile_websocket(hass: HomeAssistant) -> None:
    """Register administrator-only technology and energy-flow commands once."""

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
        entry = _entry_or_error(hass, connection, msg)
        if entry is None:
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

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_GET_ENERGY_FLOW_SETTINGS,
            vol.Required("entry_id"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_get_energy_flow_settings(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        connection.require_admin()
        entry = _entry_or_error(hass, connection, msg)
        if entry is None:
            return
        connection.send_result(msg["id"], _flow_settings(entry.options))

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_SET_ENERGY_FLOW_SETTINGS,
            vol.Required("entry_id"): str,
            vol.Required(CONF_BATTERY_POWER_SIGN): vol.In(BATTERY_POWER_SIGNS),
        }
    )
    @websocket_api.async_response
    async def websocket_set_energy_flow_settings(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        connection.require_admin()
        entry = _entry_or_error(hass, connection, msg)
        if entry is None:
            return
        settings = _flow_settings(entry.options)
        settings[CONF_BATTERY_POWER_SIGN] = str(msg[CONF_BATTERY_POWER_SIGN])
        options = dict(entry.options)
        options[CONF_ENERGY_FLOW] = settings
        hass.config_entries.async_update_entry(entry, options=options)
        connection.send_result(msg["id"], settings)

    websocket_api.async_register_command(hass, websocket_set_enabled)
    websocket_api.async_register_command(hass, websocket_get_energy_flow_settings)
    websocket_api.async_register_command(hass, websocket_set_energy_flow_settings)
    domain_data[_REGISTERED_KEY] = True
