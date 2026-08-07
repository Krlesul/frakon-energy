"""Home Assistant WebSocket API for FRAKON Energy spot prices."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .fx_rate import EurCzkRateProvider
from .spot_price_cost import SpotPriceCostConfig, calculate_spot_cost
from .spot_price_ote import OteSpotPriceProvider
from .spot_price_provider import SpotPriceProviderRuntime
from .spot_price_settings import SpotPriceSettings

COMMAND_GET_SPOT_PRICES = f"{DOMAIN}/spot_prices/get"
_RUNTIME_KEY = "spot_price_runtime"
_FX_RUNTIME_KEY = "eur_czk_rate_provider"
_REGISTERED_KEY = "spot_price_websocket_registered"


def _fetch_text_factory(hass: HomeAssistant):
    session = async_get_clientsession(hass)
    async def fetch_text(url: str) -> str:
        async with session.get(url, timeout=20) as response:
            response.raise_for_status()
            return await response.text()
    return fetch_text


def _runtime(hass: HomeAssistant) -> SpotPriceProviderRuntime:
    domain_data = hass.data.setdefault(DOMAIN, {})
    runtime = domain_data.get(_RUNTIME_KEY)
    if isinstance(runtime, SpotPriceProviderRuntime):
        return runtime
    runtime = SpotPriceProviderRuntime((OteSpotPriceProvider(_fetch_text_factory(hass)),))
    domain_data[_RUNTIME_KEY] = runtime
    return runtime


def _fx_runtime(hass: HomeAssistant) -> EurCzkRateProvider:
    domain_data = hass.data.setdefault(DOMAIN, {})
    runtime = domain_data.get(_FX_RUNTIME_KEY)
    if isinstance(runtime, EurCzkRateProvider):
        return runtime
    runtime = EurCzkRateProvider(_fetch_text_factory(hass))
    domain_data[_FX_RUNTIME_KEY] = runtime
    return runtime


def _settings(hass: HomeAssistant) -> SpotPriceSettings:
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return SpotPriceSettings()
    return SpotPriceSettings.from_options(entries[0].options)


def _enrich_day(day: dict[str, Any], settings: SpotPriceSettings, eur_czk: float) -> None:
    config = SpotPriceCostConfig(eur_czk=eur_czk, supplier_fee_czk_kwh=settings.supplier_fee_czk_kwh, variable_additions_czk_kwh=settings.variable_additions_czk_kwh, vat_percent=settings.vat_percent)
    totals: list[float] = []
    for interval in day.get("intervals", []):
        cost = calculate_spot_cost(float(interval["price_eur_mwh"]), config)
        interval["price_czk_kwh"] = cost["total_czk_kwh"]
        interval["wholesale_czk_kwh"] = cost["wholesale_czk_kwh"]
        interval["cost_breakdown"] = cost
        totals.append(cost["total_czk_kwh"])
    day["minimum_czk_kwh"] = min(totals) if totals else None
    day["maximum_czk_kwh"] = max(totals) if totals else None
    day["average_czk_kwh"] = sum(totals) / len(totals) if totals else None


@callback
def async_register_spot_price_websocket(hass: HomeAssistant) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command({vol.Required("type"): COMMAND_GET_SPOT_PRICES})
    @websocket_api.async_response
    async def websocket_get_spot_prices(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        try:
            result = await _runtime(hass).async_get(now=now)
            settings = _settings(hass)
        except Exception as err:
            connection.send_error(msg["id"], "spot_prices_unavailable", str(err))
            return
        eur_czk = settings.eur_czk
        fx_source = "manual_fallback"
        fx_error: str | None = None
        fx_fetched_at: str | None = None
        try:
            fx = await _fx_runtime(hass).async_get(now=now)
            eur_czk = fx.rate
            fx_source = fx.source
            fx_fetched_at = fx.fetched_at.isoformat()
        except Exception as err:
            fx_error = str(err)
        payload = result.snapshot.day_ahead_payload(now=now)
        _enrich_day(payload["today"], settings, eur_czk)
        _enrich_day(payload["tomorrow"], settings, eur_czk)
        payload["customer_price_settings"] = settings.as_dict()
        payload["exchange_rate"] = {"pair": "EUR/CZK", "rate": eur_czk, "source": fx_source, "fetched_at": fx_fetched_at, "fallback_used": fx_source == "manual_fallback", "error": fx_error}
        payload["provider"] = result.provider
        payload["stale"] = result.stale
        payload["fallback_used"] = result.fallback_used
        payload["provider_error"] = result.error
        connection.send_result(msg["id"], payload)

    websocket_api.async_register_command(hass, websocket_get_spot_prices)
    domain_data[_REGISTERED_KEY] = True
