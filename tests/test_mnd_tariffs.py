from datetime import date, datetime, timezone
import importlib.util
from pathlib import Path
import sys
import types

import pytest


DOCUMENT_UUID = "12345678-1234-4234-8234-123456789abc"
OFFICIAL_URL = f"https://prod.mnd.cz/documents/view/{DOCUMENT_UUID}"


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_modules():
    for name in (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.providers",
        "custom_components.frakon_energy.tariff_source_context",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.providers.mnd_tariffs",
    ):
        sys.modules.pop(name, None)
    for name in (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.providers",
    ):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    context = _load(
        "custom_components.frakon_energy.tariff_source_context",
        "custom_components/frakon_energy/tariff_source_context.py",
    )
    sources = _load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    mnd = _load(
        "custom_components.frakon_energy.providers.mnd_tariffs",
        "custom_components/frakon_energy/providers/mnd_tariffs.py",
    )
    return context, sources, mnd


def _query(
    context,
    sources,
    product: str = "Proud - Ceník Říjen 28",
    *,
    supplier: str = "mnd",
    distributor: str = "cez_distribuce",
    contract_kind: str = "fixed",
    valid_on: date = date(2026, 8, 14),
    postcode: str | None = "41201",
):
    source_context = (
        context.TariffSourceResolutionContext()
        if postcode is None
        else context.TariffSourceResolutionContext(postcode=postcode)
    )
    return sources.TariffSourceQuery(
        supplier=supplier,
        product_name=product,
        distributor=distributor,
        contract_kind=contract_kind,
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_on=valid_on,
        source_context=source_context,
    )


def _resolved(
    mnd,
    *,
    product_name: str = "Proud - Ceník Říjen 28",
    distributor: str = "cez_distribuce",
    contract_kind: str = "fixed",
    source_url: str = OFFICIAL_URL,
    valid_from: date = date(2026, 6, 11),
    valid_to: date | None = date(2028, 10, 31),
):
    return mnd.MndResolvedTariffSource(
        product_name=product_name,
        distributor=distributor,
        contract_kind=contract_kind,
        source_url=source_url,
        valid_from=valid_from,
        valid_to=valid_to,
        document_date=valid_from,
        discovered_at=datetime(2026, 8, 14, 13, 45, tzinfo=timezone.utc),
    )


class FakeResolver:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def async_resolve(self, query, product):
        self.calls.append((query, product))
        return self.result


def test_current_product_identities_are_explicit_and_contract_typed() -> None:
    _, _, mnd = load_modules()
    assert [(item.product_name, item.contract_kind) for item in mnd.MND_CURRENT_ELECTRICITY_PRODUCTS] == [
        ("Proud - Ceník Říjen 28", "fixed"),
        ("Proud - Klesající ceník Duben 29", "fixed"),
        ("Proud - Domácnosti", "indefinite"),
    ]
    assert mnd.MND_ELECTRICITY_INDEX_URL == "https://prod.mnd.cz/elektrina-domacnosti"
    assert mnd.MND_OFFICIAL_DOMAINS == ("mnd.cz",)


def test_without_exact_document_resolver_adapter_fails_closed() -> None:
    context, sources, mnd = load_modules()
    adapter = mnd.MndTariffCatalogAdapter()
    assert __import__("asyncio").run(adapter.async_discover(_query(context, sources))) == ()


def test_resolver_is_not_invoked_without_explicit_postcode_context() -> None:
    context, sources, mnd = load_modules()
    resolver = FakeResolver(_resolved(mnd))
    adapter = mnd.MndTariffCatalogAdapter(resolver=resolver)

    result = __import__("asyncio").run(
        adapter.async_discover(_query(context, sources, postcode=None))
    )

    assert result == ()
    assert resolver.calls == []


def test_exact_resolved_document_becomes_supplier_commercial_candidate_without_postcode_leak() -> None:
    context, sources, mnd = load_modules()
    resolver = FakeResolver(_resolved(mnd))
    adapter = mnd.MndTariffCatalogAdapter(resolver=resolver)
    query = _query(context, sources, postcode="412 01")

    result = __import__("asyncio").run(adapter.async_discover(query))

    assert len(result) == 1
    candidate = result[0]
    assert query.source_context.postcode == "41201"
    assert candidate.product_name == "Proud - Ceník Říjen 28"
    assert candidate.valid_from == date(2026, 6, 11)
    assert candidate.valid_to == date(2028, 10, 31)
    assert candidate.match_score == 100
    assert candidate.price_scope == sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL
    assert candidate.document.supplier == "mnd"
    assert candidate.document.source_url == OFFICIAL_URL
    assert candidate.document.content_type == "application/pdf"
    assert candidate.document.document_date == date(2026, 6, 11)
    assert "41201" not in repr(candidate)
    assert resolver.calls == [(query, mnd.MND_CURRENT_ELECTRICITY_PRODUCTS[0])]


def test_product_match_is_normalized_exact_but_never_fuzzy() -> None:
    context, sources, mnd = load_modules()
    resolver = FakeResolver(_resolved(mnd))
    adapter = mnd.MndTariffCatalogAdapter(resolver=resolver)
    exact = __import__("asyncio").run(adapter.async_discover(_query(context, sources, product="PROUD - CENIK RIJEN 28")))
    fuzzy = __import__("asyncio").run(adapter.async_discover(_query(context, sources, product="Proud Říjen 28")))
    assert len(exact) == 1
    assert fuzzy == ()
    assert len(resolver.calls) == 1


def test_contract_kind_must_match_verified_product_before_resolver_runs() -> None:
    context, sources, mnd = load_modules()
    resolver = FakeResolver(_resolved(mnd))
    adapter = mnd.MndTariffCatalogAdapter(resolver=resolver)
    assert __import__("asyncio").run(adapter.async_discover(_query(context, sources, contract_kind="indefinite"))) == ()
    assert __import__("asyncio").run(adapter.async_discover(_query(context, sources, product="Proud - Domácnosti", contract_kind="fixed"))) == ()
    assert resolver.calls == []


def test_resolver_must_return_exact_distribution_territory() -> None:
    context, sources, mnd = load_modules()
    adapter = mnd.MndTariffCatalogAdapter(resolver=FakeResolver(_resolved(mnd, distributor="eg_d")))
    with pytest.raises(ValueError, match="distribution territory"):
        __import__("asyncio").run(adapter.async_discover(_query(context, sources)))


def test_resolver_must_return_exact_verified_product_and_contract_kind() -> None:
    context, sources, mnd = load_modules()
    wrong_product = mnd.MndTariffCatalogAdapter(resolver=FakeResolver(_resolved(mnd, product_name="Proud - Domácnosti")))
    with pytest.raises(ValueError, match="product does not match"):
        __import__("asyncio").run(wrong_product.async_discover(_query(context, sources)))
    wrong_contract = mnd.MndTariffCatalogAdapter(resolver=FakeResolver(_resolved(mnd, contract_kind="indefinite")))
    with pytest.raises(ValueError, match="contract kind"):
        __import__("asyncio").run(wrong_contract.async_discover(_query(context, sources)))


def test_resolved_document_validity_must_cover_requested_day() -> None:
    context, sources, mnd = load_modules()
    resolver = FakeResolver(_resolved(mnd, valid_from=date(2026, 9, 1), valid_to=date(2028, 10, 31)))
    adapter = mnd.MndTariffCatalogAdapter(resolver=resolver)
    assert __import__("asyncio").run(adapter.async_discover(_query(context, sources))) == ()


def test_resolved_source_rejects_non_mnd_and_non_document_urls() -> None:
    _, _, mnd = load_modules()
    with pytest.raises(ValueError, match="official mnd.cz host"):
        _resolved(mnd, source_url=f"https://example.com/documents/view/{DOCUMENT_UUID}")
    with pytest.raises(ValueError, match="/documents/view"):
        _resolved(mnd, source_url="https://prod.mnd.cz/elektrina-domacnosti")
    with pytest.raises(ValueError, match="document UUID"):
        _resolved(mnd, source_url="https://prod.mnd.cz/documents/view/not-a-uuid")
    with pytest.raises(ValueError, match="query or fragment"):
        _resolved(mnd, source_url=f"{OFFICIAL_URL}?download=1")


def test_registry_revalidates_mnd_candidate_against_official_domain() -> None:
    context, sources, mnd = load_modules()
    registry = sources.TariffAdapterRegistry()
    registry.register(mnd.MndTariffCatalogAdapter(resolver=FakeResolver(_resolved(mnd))))
    candidates = __import__("asyncio").run(registry.async_discover_verified(_query(context, sources)))
    assert len(candidates) == 1
    assert candidates[0].document.source_url == OFFICIAL_URL


def test_wrong_supplier_never_invokes_mnd_resolver() -> None:
    context, sources, mnd = load_modules()
    resolver = FakeResolver(_resolved(mnd))
    adapter = mnd.MndTariffCatalogAdapter(resolver=resolver)
    assert __import__("asyncio").run(adapter.async_discover(_query(context, sources, supplier="eon"))) == ()
    assert resolver.calls == []


def test_invalid_or_duplicate_product_catalog_fails_at_construction() -> None:
    _, _, mnd = load_modules()
    duplicate = (
        mnd.MndProductDefinition("Proud - Ceník Říjen 28", "fixed"),
        mnd.MndProductDefinition("PROUD CENIK RIJEN 28", "fixed"),
    )
    with pytest.raises(ValueError, match="duplicate MND product identity"):
        mnd.MndTariffCatalogAdapter(products=duplicate)
