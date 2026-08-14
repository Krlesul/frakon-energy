"""Home Assistant adapter for the bounded tariff HTTP transport."""

from __future__ import annotations

from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .tariff_download import ValidatedTariffDownload
from .tariff_fetch import TariffFetchRequest, TariffNotModified
from .tariff_http_transport import (
    DEFAULT_TARIFF_HTTP_TIMEOUT_SECONDS,
    async_fetch_selected_tariff_document,
)
from .tariff_sources import TariffDocumentCandidate


async def async_fetch_selected_tariff_document_ha(
    hass: HomeAssistant,
    *,
    candidate: TariffDocumentCandidate,
    request: TariffFetchRequest,
    checked_at: datetime,
    timeout_seconds: float = DEFAULT_TARIFF_HTTP_TIMEOUT_SECONDS,
) -> ValidatedTariffDownload | TariffNotModified:
    """Fetch one selected tariff document through Home Assistant's shared session.

    All selection, redirect, size, conditional-request, PDF and SHA-256 checks stay
    in the transport/domain layers. This adapter owns no parallel HTTP policy.
    """
    session = async_get_clientsession(hass)
    return await async_fetch_selected_tariff_document(
        candidate=candidate,
        request=request,
        session=session,
        checked_at=checked_at,
        timeout_seconds=timeout_seconds,
    )
