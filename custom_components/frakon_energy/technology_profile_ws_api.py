from __future__ import annotations

from typing import Any, Mapping

import voluptuous as vol
from homeassistant.components import websocket_api
from .ws_auth import ensure_admin
from homeassistant.core import HomeAssistant, callback

from .const import CONF_PROVIDER, DOMAIN, PROVIDER_VISIONQ
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

COMMAND_PRIMARY_ENTRY = "frakon_energy/entry/primary"
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


def _loaded_visionq_entries(hass: HomeAssistant):
    domain_data = hass.data.get(DOMAIN, {})
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data.get(CONF_PROVIDER, PROVIDER_VISIONQ) == PROVIDER_VISIONQ
    ]
    loaded = [
        entry
        for entry in entries
        if isinstance(domain_data, Mapping) and entry.entry_id in domain_data
    ]
    return entries, loaded


@callback
def async_register_technology_profile_websocket(hass: HomeAssistant) -> None:
    """Register administrator-only technology and energy-flow commands once."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_PRIMARY_ENTRY,
        }
    )
    @websocket_api.async_response
    async def websocket_primary_entry(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        ensure_admin(connection)
        entries, loaded = _loaded_visionq_entries(hass)
        if not loaded:
            connection.send_error(
                msg["id"],
                "visionq_runtime_unavailable",
                (
                    "Není načtená žádná VisionQ položka FRAKON Energy. "
                    f"Nalezeno VisionQ konfigurací: {len(entries)}."
                ),
            )
            return
        if len(loaded) != 1:
            connection.send_error(
                msg["id"],
                "ambiguous_visionq_runtime",
                (
                    "Je načteno více VisionQ položek FRAKON Energy; nelze bezpečně "
                    f"zvolit hlavní instanci ({len(loaded)})."
                ),
            )
            return
        entry = loaded[0]
        connection.send_result(
            msg["id"],
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "provider": PROVIDER_VISIONQ,
                "loaded": True,
                "visionq_entry_count": len(entries),
                "loaded_visionq_entry_count": len(loaded),
            },
        )

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
        ensure_admin(connection)
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
        ensure_admin(connection)
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
        ensure_admin(connection)
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

    websocket_api.async_register_command(hass, websocket_primary_entry)
    websocket_api.async_register_command(hass, websocket_set_enabled)
    websocket_api.async_register_command(hass, websocket_get_energy_flow_settings)
    websocket_api.async_register_command(hass, websocket_set_energy_flow_settings)
    domain_data[_REGISTERED_KEY] = True
