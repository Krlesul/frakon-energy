from datetime import date, datetime, timezone
import importlib.util
from pathlib import Path
import sys
import types


def load_modules():
    for name in (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.providers",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.providers.cez_tariffs",
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

    source_path = Path("custom_components/frakon_energy/tariff_sources.py")
    source_spec = importlib.util.spec_from_file_location(
        "custom_components.frakon_energy.tariff_sources", source_path
    )
    source_module = importlib.util.module_from_spec(source_spec)
    sys.modules[source_spec.name] = source_module
    source_spec.loader.exec_module(source_module)

    cez_path = Path("custom_components/frakon_energy/providers/cez_tariffs.py")
    cez_spec = importlib.util.spec_from_file_location(
        "custom_components.frakon_energy.providers.cez_tariffs", cez_path
    )
    cez_module = importlib.util.module_from_spec(cez_spec)
    sys.modules[cez_spec.name] = cez_module
    cez_spec.loader.exec_module(cez_module)
    return source_module, cez_module


def _query(source_module, product: str, *, valid_on: date = date(2026, 8, 14)):
    return source_module.TariffSourceQuery(
        supplier="cez",
        product_name=product,
        distributor="cez_distribuce",
        contract_kind="indefinite",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_on=valid_on,
    )


def _clock():
    return datetime(2026, 8, 14, 4, 30, tzinfo=timezone.utc)


def test_verified_catalog_contains_seven_official_cez_pdfs() -> None:
    _, cez = load_modules()

    assert len(cez.CEZ_2026_COMMERCIAL_CATALOG) == 7
    assert {item.product_name for item in cez.CEZ_2026_COMMERCIAL_CATALOG} == {
        "Elektřina na dobu neurčitou",
        "Basic",
        "eTarif",
        "Zelená elektřina",
        "Elektřina pro ZTP",
        "Krátko odběr",
        "Elektřina bez závazku",
    }
    for item in cez.CEZ_2026_COMMERCIAL_CATALOG:
        assert item.source_url.startswith("https://www.cez.cz/file/")
        assert item.source_url.endswith(".pdf")
        assert item.valid_from == date(2026, 1, 1)


def test_exact_product_and_official_alias_return_commercial_only_candidate() -> None:
    sources, cez = load_modules()
    adapter = cez.CezTariffCatalogAdapter(clock=_clock)

    canonical = __import__("asyncio").run(
        adapter.async_discover(_query(sources, "Basic"))
    )
    alias = __import__("asyncio").run(
        adapter.async_discover(_query(sources, "Elektřina Basic"))
    )

    assert len(canonical) == 1
    assert canonical[0].match_score == 100
    assert len(alias) == 1
    assert alias[0].match_score == 98
    for candidate in (canonical[0], alias[0]):
        assert candidate.product_name == "Basic"
        assert candidate.price_scope == sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL
        assert candidate.document.supplier == "cez"
        assert candidate.document.content_type == "application/pdf"
        assert "regulated components are separate" in candidate.match_reasons[-1]


def test_product_matching_is_accent_case_normalized_but_fail_closed() -> None:
    sources, cez = load_modules()
    adapter = cez.CezTariffCatalogAdapter(clock=_clock)

    matched = __import__("asyncio").run(
        adapter.async_discover(_query(sources, "ZELENA ELEKTRINA"))
    )
    unknown = __import__("asyncio").run(
        adapter.async_discover(_query(sources, "nějaký podobný produkt"))
    )

    assert [item.product_name for item in matched] == ["Zelená elektřina"]
    assert unknown == ()


def test_catalog_never_applies_before_document_validity() -> None:
    sources, cez = load_modules()
    adapter = cez.CezTariffCatalogAdapter(clock=_clock)

    result = __import__("asyncio").run(
        adapter.async_discover(
            _query(sources, "Elektřina na dobu neurčitou", valid_on=date(2025, 12, 31))
        )
    )
    assert result == ()


def test_registry_verifies_cez_adapter_output_against_official_domain() -> None:
    sources, cez = load_modules()
    registry = sources.TariffAdapterRegistry()
    registry.register(cez.CezTariffCatalogAdapter(clock=_clock))

    candidates = __import__("asyncio").run(
        registry.async_discover_verified(_query(sources, "Elektřina eTarif"))
    )

    assert len(candidates) == 1
    assert candidates[0].product_name == "eTarif"
    assert candidates[0].document.source_url == (
        "https://www.cez.cz/file/edee/2025/10/x03_moo_ee_etarif.pdf"
    )


def test_wrong_supplier_query_returns_no_cez_candidate() -> None:
    sources, cez = load_modules()
    adapter = cez.CezTariffCatalogAdapter(clock=_clock)
    query = sources.TariffSourceQuery(
        supplier="eon",
        product_name="Basic",
        distributor="eg_d",
        contract_kind="indefinite",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_on=date(2026, 8, 14),
    )

    assert __import__("asyncio").run(adapter.async_discover(query)) == ()
