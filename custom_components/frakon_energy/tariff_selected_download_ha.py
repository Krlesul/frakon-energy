"""Home Assistant adapter for exact selected tariff document downloads."""

from __future__ import annotations

from datetime import date, datetime

from homeassistant.core import HomeAssistant

from .contracts import ElectricityContract
from .tariff_adapter_registry import build_default_tariff_adapter_registry
from .tariff_http_ha import async_fetch_selected_tariff_document_ha
from .tariff_selected_download import (
    SelectedTariffDownloadRun,
    async_fetch_selected_contract_tariff,
)
from .tariff_sources import TariffAdapterRegistry


async def async_fetch_selected_contract_tariff_ha(
    hass: HomeAssistant,
    *,
    contract: ElectricityContract,
    day: date,
    selected_fingerprint: str,
    checked_at: datetime,
    registry: TariffAdapterRegistry | None = None,
) -> SelectedTariffDownloadRun:
    """Re-discover and fetch one explicit tariff selection through HA shared HTTP.

    The optional registry exists for tests and future runtime reuse. Production
    callers that omit it receive the canonical four-supplier registry.
    """
    if registry is None:
        registry = build_default_tariff_adapter_registry()

    async def fetch_selected(*, candidate, request, checked_at):
        return await async_fetch_selected_tariff_document_ha(
            hass,
            candidate=candidate,
            request=request,
            checked_at=checked_at,
        )

    return await async_fetch_selected_contract_tariff(
        contract,
        day=day,
        selected_fingerprint=selected_fingerprint,
        registry=registry,
        checked_at=checked_at,
        fetch_selected=fetch_selected,
    )
