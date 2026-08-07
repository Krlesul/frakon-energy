"""WebSocket API for persistent FRAKON Energy spot-price settings."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .spot_price_settings import FX_MODE_AUTO, FX_MODE_MANUAL, SpotPriceSettings

COMMAND_GET = f"{DOMAIN}/spot_price_settings/get"
COMMAND_SET = f"{DOMAIN}/spot_price_settings/set"
_REGISTERED_KEY = "spot_price_settings_websocket_registered"


def _entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ValueError("FRAKON Energy config entry not found")
    return entry


@callback
def async_register_spot_price_settings_websocket(hass: HomeAssistant) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command({vol.Required("type"): COMMAND_GET, vol.Required("entry_id"): str})
    @websocket_api.async_response
    async def websocket_get(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
        try:
            settings = SpotPriceSettings.from_options(_entry(hass, msg["entry_id"]).options)
        except (ValueError, TypeError) as err:
            connection.send_error(msg["id"], "invalid_spot_price_settings", str(err))
            return
        connection.send_result(msg["id"], settings.as_dict())

    @websocket_api.websocket_command({
        vol.Required("type"): COMMAND_SET,
        vol.Required("entry_id"): str,
        vol.Required("eur_czk"): vol.Coerce(float),
        vol.Optional("fx_mode", default=FX_MODE_AUTO): vol.In([FX_MODE_AUTO, FX_MODE_MANUAL]),
        vol.Required("supplier_fee_czk_kwh"): vol.Coerce(float),
        vol.Required("variable_additions_czk_kwh"): vol.Coerce(float),
        vol.Required("vat_percent"): vol.Coerce(float),
    })
    @websocket_api.async_response
    async def websocket_set(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
        try:
            entry = _entry(hass, msg["entry_id"])
            settings = SpotPriceSettings(
                eur_czk=msg["eur_czk"],
                fx_mode=msg["fx_mode"],
                supplier_fee_czk_kwh=msg["supplier_fee_czk_kwh"],
                variable_additions_czk_kwh=msg["variable_additions_czk_kwh"],
                vat_percent=msg["vat_percent"],
            ).validated()
        except (ValueError, TypeError) as err:
            connection.send_error(msg["id"], "invalid_spot_price_settings", str(err))
            return
        options = dict(entry.options)
        options.update(settings.option_values())
        hass.config_entries.async_update_entry(entry, options=options)
        connection.send_result(msg["id"], settings.as_dict())

    websocket_api.async_register_command(hass, websocket_get)
    websocket_api.async_register_command(hass, websocket_set)
    domain_data[_REGISTERED_KEY] = True
