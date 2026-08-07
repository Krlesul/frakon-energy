"""Home Assistant WebSocket API for FRAKON Energy spot prices."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .spot_price_ote import OteSpotPriceProvider
from .spot_price_provider import SpotPriceProviderRuntime

COMMAND_GET_SPOT_PRICES = f"{DOMAIN}/spot_prices/get"
_RUNTIME_KEY = "spot_price_runtime"
_REGISTERED_KEY = "spot_price_websocket_registered"


def _runtime(hass: HomeAssistant) -> SpotPriceProviderRuntime:
    domain_data = hass.data.setdefault(DOMAIN, {})
    runtime = domain_data.get(_RUNTIME_KEY)
    if isinstance(runtime, SpotPriceProviderRuntime):
        return runtime

    session = async_get_clientsession(hass)

    async def fetch_text(url: str) -> str:
        async with session.get(url, timeout=20) as response:
            response.raise_for_status()
            return await response.text()

    runtime = SpotPriceProviderRuntime((OteSpotPriceProvider(fetch_text),))
    domain_data[_RUNTIME_KEY] = runtime
    return runtime


@callback
def async_register_spot_price_websocket(hass: HomeAssistant) -> None:
    """Register the dashboard spot-price command once per Home Assistant instance."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command(
        {vol.Required("type"): COMMAND_GET_SPOT_PRICES}
    )
    @websocket_api.async_response
    async def websocket_get_spot_prices(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        now = datetime.now(timezone.utc)
        try:
            result = await _runtime(hass).async_get(now=now)
        except Exception as err:
            connection.send_error(msg["id"], "spot_prices_unavailable", str(err))
            return

        payload = result.snapshot.day_ahead_payload(now=now)
        payload["provider"] = result.provider
        payload["stale"] = result.stale
        payload["fallback_used"] = result.fallback_used
        payload["provider_error"] = result.error
        connection.send_result(msg["id"], payload)

    websocket_api.async_register_command(hass, websocket_get_spot_prices)
    domain_data[_REGISTERED_KEY] = True
