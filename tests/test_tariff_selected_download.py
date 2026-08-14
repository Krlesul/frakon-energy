import asyncio
from datetime import date, datetime, timezone
import importlib.util
from pathlib import Path
import sys
import types

import pytest


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_modules():
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.contracts",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components.frakon_energy.tariff_download",
        "custom_components.frakon_energy.tariff_fetch",
        "custom_components.frakon_energy.tariff_discovery",
        "custom_components.frakon_energy.tariff_selected_download",
    )
    for name in names:
        sys.modules.pop(name, None)

    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    contracts = _load(
        "custom_components.frakon_energy.contracts",
        "custom_components/frakon_energy/contracts.py",
    )
    sources = _load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    selection = _load(
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components/frakon_energy/tariff_candidate_selection.py",
    )
    download = _load(
        "custom_components.frakon_energy.tariff_download",
        "custom_components/frakon_energy/tariff_download.py",
    )
    fetch = _load(
        "custom_components.frakon_energy.tariff_fetch",
        "custom_components/frakon_energy/tariff_fetch.py",
    )
    discovery = _load(
        "custom_components.frakon_energy.tariff_discovery",
        "custom_components/frakon_energy/tariff_discovery.py",
    )
    selected = _load(
        "custom_components.frakon_energy.tariff_selected_download",
        "custom_components/frakon_energy/tariff_selected_download.py",
    )
    return contracts, sources, selection, download, fetch, discovery, selected


DAY = date(2026, 8, 14)
CHECKED_AT = datetime(2026, 8, 14, 16, 30, tzinfo=timezone.utc)


def _contract(contracts):
    return contracts.ElectricityContract(
        supplier=contracts.Supplier.CEZ,
        distributor=contracts.Distributor.CEZ_DISTRIBUCE,
        product_name="Basic",
        contract_kind=contracts.ContractKind.INDEFINITE,
        distribution_tariff="D25d",
        breaker=contracts.Breaker(phases=3, amperes=25),
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        customer_confirmed=False,
    )


def _candidate(sources, query, *, url="https://www.cez.cz/file/verified.pdf", etag=None):
    return sources.TariffDocumentCandidate(
        document=sources.OfficialTariffDocument(
            supplier=query.supplier,
            source_url=url,
            discovered_at=datetime(2026, 8, 14, 16, 0, tzinfo=timezone.utc),
            document_date=date(2026, 1, 1),
            etag=etag,
            content_type="application/pdf",
        ),
        product_name=query.product_name,
        valid_from=date(2026, 1, 1),
        match_score=100,
        match_reasons=("exact current candidate",),
        price_scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    )


class StaticAdapter:
    supplier = "cez"
    official_domains = ("cez.cz",)

    def __init__(self, candidate):
        self.candidate = candidate
        self.calls = []

    async def async_discover(self, query):
        self.calls.append(query)
        return (self.candidate,)


def _registry(sources, candidate):
    adapter = StaticAdapter(candidate)
    registry = sources.TariffAdapterRegistry()
    registry.register(adapter)
    return registry, adapter


def test_selected_download_rediscovers_current_candidate_before_fetch() -> None:
    contracts, sources, selection, download, _fetch, discovery, selected = load_modules()
    contract = _contract(contracts)
    query = discovery.tariff_source_query_from_contract(contract, day=DAY)
    candidate = _candidate(sources, query)
    fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)
    registry, adapter = _registry(sources, candidate)
    calls = []

    async def fetch_selected(*, candidate, request, checked_at):
        calls.append((candidate, request, checked_at))
        return download.validate_selected_tariff_download(
            candidate=candidate,
            selected_fingerprint=request.selected_fingerprint,
            status_code=200,
            final_url=request.source_url,
            content_type="application/pdf; charset=binary",
            content=b"%PDF-1.7\nverified tariff\n%%EOF",
            validated_at=checked_at,
            etag='"v1"',
        )

    run = asyncio.run(
        selected.async_fetch_selected_contract_tariff(
            contract,
            day=DAY,
            selected_fingerprint=fingerprint,
            registry=registry,
            checked_at=CHECKED_AT,
            fetch_selected=fetch_selected,
        )
    )

    assert len(adapter.calls) == 1
    assert adapter.calls[0].distribution_tariff == "D25d"
    assert adapter.calls[0].breaker_code == "3x25A"
    assert len(calls) == 1
    fetched_candidate, request, checked_at = calls[0]
    assert fetched_candidate is candidate
    assert request.selected_fingerprint == fingerprint
    assert request.source_url == candidate.document.source_url
    assert request.allow_redirects is False
    assert checked_at == CHECKED_AT
    assert run.candidate is candidate
    assert run.outcome.document.sha256 is not None
    assert run.body_available is True
    assert run.parser_authorized is True
    assert run.persistence_performed is False
    assert run.activation_performed is False


def test_stale_ui_fingerprint_cannot_authorize_newly_discovered_candidate() -> None:
    contracts, sources, selection, _download, _fetch, discovery, selected = load_modules()
    contract = _contract(contracts)
    query = discovery.tariff_source_query_from_contract(contract, day=DAY)
    stale = _candidate(sources, query, url="https://www.cez.cz/file/old.pdf")
    current = _candidate(sources, query, url="https://www.cez.cz/file/current.pdf")
    stale_fingerprint = selection.tariff_candidate_selection_fingerprint(stale)
    registry, _adapter = _registry(sources, current)
    fetch_calls = []

    async def fetch_selected(**kwargs):
        fetch_calls.append(kwargs)
        raise AssertionError("stale selection must stop before HTTP")

    with pytest.raises(LookupError, match="tariff candidate not found"):
        asyncio.run(
            selected.async_fetch_selected_contract_tariff(
                contract,
                day=DAY,
                selected_fingerprint=stale_fingerprint,
                registry=registry,
                checked_at=CHECKED_AT,
                fetch_selected=fetch_selected,
            )
        )

    assert fetch_calls == []


def test_conditional_not_modified_result_never_authorizes_parser() -> None:
    contracts, sources, selection, _download, fetch, discovery, selected = load_modules()
    contract = _contract(contracts)
    query = discovery.tariff_source_query_from_contract(contract, day=DAY)
    candidate = _candidate(sources, query, etag='"known"')
    fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)
    registry, _adapter = _registry(sources, candidate)

    async def fetch_selected(*, candidate, request, checked_at):
        assert request.conditional is True
        assert request.headers_dict()["If-None-Match"] == '"known"'
        return fetch.TariffNotModified(
            selected_fingerprint=request.selected_fingerprint,
            source_url=request.source_url,
            checked_at=checked_at,
            etag='"known"',
            last_modified=None,
        )

    run = asyncio.run(
        selected.async_fetch_selected_contract_tariff(
            contract,
            day=DAY,
            selected_fingerprint=fingerprint,
            registry=registry,
            checked_at=CHECKED_AT,
            fetch_selected=fetch_selected,
        )
    )

    assert run.body_available is False
    assert run.parser_authorized is False
    assert run.outcome.body_downloaded is False
    assert run.outcome.activation_performed is False


def test_invalid_fetcher_outcome_fails_closed() -> None:
    contracts, sources, selection, _download, _fetch, discovery, selected = load_modules()
    contract = _contract(contracts)
    query = discovery.tariff_source_query_from_contract(contract, day=DAY)
    candidate = _candidate(sources, query)
    fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)
    registry, _adapter = _registry(sources, candidate)

    async def fetch_selected(**_kwargs):
        return object()

    with pytest.raises(ValueError, match="invalid outcome"):
        asyncio.run(
            selected.async_fetch_selected_contract_tariff(
                contract,
                day=DAY,
                selected_fingerprint=fingerprint,
                registry=registry,
                checked_at=CHECKED_AT,
                fetch_selected=fetch_selected,
            )
        )


def test_naive_checked_at_fails_before_discovery_or_http() -> None:
    contracts, sources, selection, _download, _fetch, discovery, selected = load_modules()
    contract = _contract(contracts)
    query = discovery.tariff_source_query_from_contract(contract, day=DAY)
    candidate = _candidate(sources, query)
    fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)
    registry, adapter = _registry(sources, candidate)
    fetch_calls = []

    async def fetch_selected(**kwargs):
        fetch_calls.append(kwargs)
        return object()

    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(
            selected.async_fetch_selected_contract_tariff(
                contract,
                day=DAY,
                selected_fingerprint=fingerprint,
                registry=registry,
                checked_at=datetime(2026, 8, 14, 16, 30),
                fetch_selected=fetch_selected,
            )
        )

    assert adapter.calls == []
    assert fetch_calls == []
