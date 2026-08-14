"""Administrator-only tariff product catalog websocket API."""

from __future__ import annotations

from typing import Any, Mapping

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .tariff_product_catalog import tariff_product_catalog_payload

COMMAND_TARIFF_PRODUCT_CATALOG = "frakon_energy/tariff/catalog"
_REGISTERED_KEY = "tariff_product_catalog_websocket_registered"


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
def async_register_tariff_product_catalog_websocket(hass: HomeAssistant) -> None:
    """Register the deterministic read-only wizard catalog command once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_TARIFF_PRODUCT_CATALOG,
            vol.Required("entry_id"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_tariff_product_catalog(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        connection.require_admin()
        entry = _entry_or_error(hass, connection, msg)
        if entry is None:
            return

        payload = tariff_product_catalog_payload()
        connection.send_result(
            msg["id"],
            {
                "entry_id": entry.entry_id,
                **payload,
                "download_performed": False,
                "parsing_performed": False,
                "persistence_performed": False,
            },
        )

    websocket_api.async_register_command(hass, websocket_tariff_product_catalog)
    domain_data[_REGISTERED_KEY] = True
