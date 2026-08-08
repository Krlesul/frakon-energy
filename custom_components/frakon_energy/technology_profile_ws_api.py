from __future__ import annotations

from typing import Any, Mapping

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .energy_flow_settings import (
    BATTERY_POWER_SIGNS,
    CONF_BATTERY_POWER_SIGN,
    CONF_ENERGY_FLOW,
    CONF_EV_WALLBOX_RELATION,
    CONF_GRID_METER_SCOPE,
    CONF_PV_POWER_SCOPE,
    EV_WALLBOX_RELATIONS,
    GRID_METER_SCOPES,
    PV_POWER_SCOPES,
    flow_settings_from_options,
)
from .technology_profile import HouseTechnology
from .technology_profile_storage import update_technology_enabled

COMMAND_SET_TECHNOLOGY_ENABLED = "frakon_energy/technology/set_enabled"
COMMAND_GET_ENERGY_FLOW_SETTINGS = "frakon_energy/energy_flow/get"
COMMAND_SET_ENERGY_FLOW_SETTINGS = "frakon_energy/energy_flow/set"
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
        connection.send_result(msg["id"], flow_settings_from_options(entry.options))

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_SET_ENERGY_FLOW_SETTINGS,
            vol.Required("entry_id"): str,
            vol.Optional(CONF_BATTERY_POWER_SIGN): vol.In(BATTERY_POWER_SIGNS),
            vol.Optional(CONF_GRID_METER_SCOPE): vol.In(GRID_METER_SCOPES),
            vol.Optional(CONF_PV_POWER_SCOPE): vol.In(PV_POWER_SCOPES),
            vol.Optional(CONF_EV_WALLBOX_RELATION): vol.In(EV_WALLBOX_RELATIONS),
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
        settings = flow_settings_from_options(entry.options)
        for key in (
            CONF_BATTERY_POWER_SIGN,
            CONF_GRID_METER_SCOPE,
            CONF_PV_POWER_SCOPE,
            CONF_EV_WALLBOX_RELATION,
        ):
            if key in msg:
                settings[key] = str(msg[key])
        options = dict(entry.options)
        options[CONF_ENERGY_FLOW] = settings
        hass.config_entries.async_update_entry(entry, options=options)
        connection.send_result(msg["id"], settings)

    websocket_api.async_register_command(hass, websocket_set_enabled)
    websocket_api.async_register_command(hass, websocket_get_energy_flow_settings)
    websocket_api.async_register_command(hass, websocket_set_energy_flow_settings)
    domain_data[_REGISTERED_KEY] = True
