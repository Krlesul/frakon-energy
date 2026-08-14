import asyncio
from datetime import date, datetime, timezone
import importlib.util
from pathlib import Path
import sys
import types


def load_modules():
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components.frakon_energy.tariff_download",
        "custom_components.frakon_energy.tariff_fetch",
        "custom_components.frakon_energy.tariff_http_transport",
        "custom_components.frakon_energy.tariff_http_ha",
        "homeassistant",
        "homeassistant.core",
        "homeassistant.helpers",
        "homeassistant.helpers.aiohttp_client",
    )
    for name in names:
        sys.modules.pop(name, None)

    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    def load(name: str, path: str):
        spec = importlib.util.spec_from_file_location(name, Path(path))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    sources = load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    load(
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components/frakon_energy/tariff_candidate_selection.py",
    )
    load(
        "custom_components.frakon_energy.tariff_download",
        "custom_components/frakon_energy/tariff_download.py",
    )
    fetch = load(
        "custom_components.frakon_energy.tariff_fetch",
        "custom_components/frakon_energy/tariff_fetch.py",
    )
    transport = load(
        "custom_components.frakon_energy.tariff_http_transport",
        "custom_components/frakon_energy/tariff_http_transport.py",
    )

    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    sys.modules["homeassistant"] = homeassistant

    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:
        pass

    core.HomeAssistant = HomeAssistant
    sys.modules["homeassistant.core"] = core

    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    sys.modules["homeassistant.helpers"] = helpers

    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    shared_session = object()
    session_calls = []

    def async_get_clientsession(hass):
        session_calls.append(hass)
        return shared_session

    aiohttp_client.async_get_clientsession = async_get_clientsession
    sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client

    transport_calls = []
    sentinel = object()

    async def fake_transport(**kwargs):
        transport_calls.append(kwargs)
        return sentinel

    transport.async_fetch_selected_tariff_document = fake_transport
    adapter = load(
        "custom_components.frakon_energy.tariff_http_ha",
        "custom_components/frakon_energy/tariff_http_ha.py",
    )
    return (
        sources,
        fetch,
        transport,
        adapter,
        HomeAssistant,
        shared_session,
        session_calls,
        transport_calls,
        sentinel,
    )


def _candidate(sources):
    return sources.TariffDocumentCandidate(
        document=sources.OfficialTariffDocument(
            supplier="cez",
            source_url="https://www.cez.cz/file/cenik.pdf",
            discovered_at=datetime(2026, 8, 14, 6, 0, tzinfo=timezone.utc),
            content_type="application/pdf",
        ),
        product_name="Basic",
        valid_from=date(2026, 1, 1),
        match_score=100,
        match_reasons=("exact product",),
        price_scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    )


def _request(fetch, candidate):
    # The wrapper does not reconstruct policy. Use a valid request object that
    # mirrors what the already-tested builder would produce.
    return fetch.TariffFetchRequest(
        selected_fingerprint="a" * 64,
        source_url=candidate.document.source_url,
        headers=(("Accept", "application/pdf"),),
        allow_redirects=False,
    )


def test_ha_adapter_uses_shared_session_and_delegates_exact_arguments() -> None:
    (
        sources,
        fetch,
        transport,
        adapter,
        HomeAssistant,
        shared_session,
        session_calls,
        transport_calls,
        sentinel,
    ) = load_modules()
    hass = HomeAssistant()
    candidate = _candidate(sources)
    request = _request(fetch, candidate)
    checked_at = datetime(2026, 8, 14, 7, 15, tzinfo=timezone.utc)

    result = asyncio.run(
        adapter.async_fetch_selected_tariff_document_ha(
            hass,
            candidate=candidate,
            request=request,
            checked_at=checked_at,
            timeout_seconds=7.5,
        )
    )

    assert result is sentinel
    assert session_calls == [hass]
    assert transport_calls == [
        {
            "candidate": candidate,
            "request": request,
            "session": shared_session,
            "checked_at": checked_at,
            "timeout_seconds": 7.5,
        }
    ]
    assert adapter.DEFAULT_TARIFF_HTTP_TIMEOUT_SECONDS == (
        transport.DEFAULT_TARIFF_HTTP_TIMEOUT_SECONDS
    )


def test_ha_adapter_uses_transport_default_timeout_when_not_overridden() -> None:
    (
        sources,
        fetch,
        transport,
        adapter,
        HomeAssistant,
        shared_session,
        session_calls,
        transport_calls,
        sentinel,
    ) = load_modules()
    hass = HomeAssistant()
    candidate = _candidate(sources)
    request = _request(fetch, candidate)
    checked_at = datetime(2026, 8, 14, 7, 20, tzinfo=timezone.utc)

    result = asyncio.run(
        adapter.async_fetch_selected_tariff_document_ha(
            hass,
            candidate=candidate,
            request=request,
            checked_at=checked_at,
        )
    )

    assert result is sentinel
    assert session_calls == [hass]
    assert transport_calls[0]["session"] is shared_session
    assert transport_calls[0]["timeout_seconds"] == (
        transport.DEFAULT_TARIFF_HTTP_TIMEOUT_SECONDS
    )
