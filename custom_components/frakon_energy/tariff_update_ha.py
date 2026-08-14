"""Home Assistant adapter for durable active tariff source checks."""

from __future__ import annotations

from datetime import date, datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .tariff_http_transport import DEFAULT_TARIFF_HTTP_TIMEOUT_SECONDS
from .tariff_update_orchestrator import (
    TariffUpdateCheckRun,
    async_check_active_tariff_source,
)


async def async_check_active_tariff_source_ha(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    day: date,
    checked_at: datetime,
    timeout_seconds: float = DEFAULT_TARIFF_HTTP_TIMEOUT_SECONDS,
) -> TariffUpdateCheckRun:
    """Run one active tariff source check and persist only its durable options state.

    The pure orchestrator owns all contract/catalog/watch safety rules. This HA
    adapter only provides Home Assistant's shared HTTP session and writes the
    returned options snapshot when it actually differs from the config entry.
    It never confirms or activates a tariff and never parses changed PDF bytes.
    """
    if not isinstance(day, date):
        raise ValueError("day must be a date")
    if not isinstance(checked_at, datetime) or checked_at.tzinfo is None:
        raise ValueError("checked_at must be a timezone-aware datetime")

    session = async_get_clientsession(hass)
    run = await async_check_active_tariff_source(
        entry.options,
        day=day,
        session=session,
        checked_at=checked_at,
        timeout_seconds=timeout_seconds,
    )
    if dict(entry.options) != run.updated_options:
        hass.config_entries.async_update_entry(
            entry,
            options=run.updated_options,
        )
    return run
