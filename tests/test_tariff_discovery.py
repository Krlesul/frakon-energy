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
        "custom_components.frakon_energy.tariff_discovery",
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
    discovery = _load(
        "custom_components.frakon_energy.tariff_discovery",
        "custom_components/frakon_energy/tariff_discovery.py",
    )
    return contracts, sources, selection, discovery


def _contract(contracts, *, confirmed: bool = False):
    return contracts.ElectricityContract(
        supplier=contracts.Supplier.CEZ,
        distributor=contracts.Distributor.CEZ_DISTRIBUCE,
        product_name="Basic",
        contract_kind=contracts.ContractKind.INDEFINITE,
        distribution_tariff="D25d",
        breaker=contracts.Breaker(phases=3, amperes=25),
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        customer_confirmed=confirmed,
    )


def _candidate(sources, query):
    return sources.TariffDocumentCandidate(
        document=sources.OfficialTariffDocument(
            supplier=query.supplier,
            source_url="https://www.cez.cz/file/verified.pdf",
            discovered_at=datetime(2026, 8, 14, 14, 30, tzinfo=timezone.utc),
            document_date=date(2026, 1, 1),
            content_type="application/pdf",
        ),
        product_name=query.product_name,
        valid_from=date(2026, 1, 1),
        valid_to=None,
        match_score=100,
        match_reasons=("exact test candidate",),
        price_scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    )


class RecordingAdapter:
    supplier = "cez"
    official_domains = ("cez.cz",)
    catalog_index_url = "https://www.cez.cz/cs/nove-ceny"

    def __init__(self, sources):
        self.sources = sources
        self.calls = []

    async def async_discover(self, query):
        self.calls.append(query)
        return (_candidate(self.sources, query),)


def test_contract_bridge_preserves_all_supplier_lookup_dimensions() -> None:
    contracts, _sources, _selection, discovery = load_modules()
    contract = _contract(contracts)

    query = discovery.tariff_source_query_from_contract(
        contract,
        day=date(2026, 8, 14),
    )

    assert query.supplier == "cez"
    assert query.product_name == "Basic"
    assert query.distributor == "cez_distribuce"
    assert query.contract_kind == "indefinite"
    assert query.distribution_tariff == "D25d"
    assert query.breaker_code == "3x25A"
    assert query.valid_on == date(2026, 8, 14)


def test_pre_activation_contract_is_allowed_for_review_discovery() -> None:
    contracts, sources, _selection, discovery = load_modules()
    contract = _contract(contracts, confirmed=False)
    adapter = RecordingAdapter(sources)
    registry = sources.TariffAdapterRegistry()
    registry.register(adapter)

    candidates = __import__("asyncio").run(
        discovery.async_discover_contract_tariff_candidates(
            contract,
            day=date(2026, 8, 14),
            registry=registry,
        )
    )

    assert len(candidates) == 1
    assert candidates[0].document.supplier == "cez"
    assert candidates[0].product_name == "Basic"
    assert len(adapter.calls) == 1
    assert adapter.calls[0].breaker_code == "3x25A"


def test_review_bridge_exposes_only_non_authoritative_candidate_summary() -> None:
    contracts, sources, selection, discovery = load_modules()
    contract = _contract(contracts)
    adapter = RecordingAdapter(sources)
    registry = sources.TariffAdapterRegistry()
    registry.register(adapter)

    items = __import__("asyncio").run(
        discovery.async_discover_contract_tariff_review(
            contract,
            day=date(2026, 8, 14),
            registry=registry,
        )
    )

    assert len(items) == 1
    item = items[0]
    assert isinstance(item, selection.TariffCandidateReviewItem)
    assert item.supplier == "cez"
    assert item.product_name == "Basic"
    assert item.source_url == "https://www.cez.cz/file/verified.pdf"
    assert item.download_performed is False
    assert item.parsing_performed is False
    assert item.persistence_performed is False
    assert item.activation_performed is False


def test_discovery_rejects_day_outside_contract_version() -> None:
    contracts, sources, _selection, discovery = load_modules()
    contract = _contract(contracts)
    registry = sources.TariffAdapterRegistry()
    registry.register(RecordingAdapter(sources))

    with pytest.raises(ValueError, match="contract does not apply"):
        __import__("asyncio").run(
            discovery.async_discover_contract_tariff_candidates(
                contract,
                day=date(2027, 1, 1),
                registry=registry,
            )
        )


def test_discovery_requires_real_registry_and_contract_types() -> None:
    contracts, sources, _selection, discovery = load_modules()
    contract = _contract(contracts)

    with pytest.raises(ValueError, match="registry must be TariffAdapterRegistry"):
        __import__("asyncio").run(
            discovery.async_discover_contract_tariff_candidates(
                contract,
                day=date(2026, 8, 14),
                registry=object(),
            )
        )

    with pytest.raises(ValueError, match="contract must be ElectricityContract"):
        discovery.tariff_source_query_from_contract(
            object(),
            day=date(2026, 8, 14),
        )

    assert isinstance(sources.TariffAdapterRegistry(), sources.TariffAdapterRegistry)
