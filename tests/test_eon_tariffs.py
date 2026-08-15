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
        "custom_components.frakon_energy.providers.eon_tariffs",
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

    eon_path = Path("custom_components/frakon_energy/providers/eon_tariffs.py")
    eon_spec = importlib.util.spec_from_file_location(
        "custom_components.frakon_energy.providers.eon_tariffs", eon_path
    )
    eon_module = importlib.util.module_from_spec(eon_spec)
    sys.modules[eon_spec.name] = eon_module
    eon_spec.loader.exec_module(eon_module)
    return source_module, eon_module


def _query(
    source_module,
    product: str,
    *,
    distributor: str = "eg_d",
    valid_on: date = date(2026, 8, 14),
    contract_kind: str = "fixed",
    supplier: str = "eon",
):
    return source_module.TariffSourceQuery(
        supplier=supplier,
        product_name=product,
        distributor=distributor,
        contract_kind=contract_kind,
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_on=valid_on,
    )


def _clock():
    return datetime(2026, 8, 14, 12, 30, tzinfo=timezone.utc)


def test_verified_catalog_contains_two_products_for_all_three_distributors() -> None:
    _, eon = load_modules()

    catalog = eon.EON_2026_ELECTRICITY_CATALOG
    assert len(catalog) == 9
    assert {item.product_name for item in catalog} == {
        "Variant PRO na 2 roky",
        "Elektřina výhodně PRO na 3 roky",
    }
    assert {item.distributor for item in catalog} == {
        "eg_d",
        "cez_distribuce",
        "pre_distribuce",
    }
    assert sum(item.product_name == "Variant PRO na 2 roky" for item in catalog) == 3
    assert sum(
        item.product_name == "Elektřina výhodně PRO na 3 roky" for item in catalog
    ) == 6
    assert {
        (item.valid_from, item.valid_to)
        for item in catalog
        if item.product_name == "Elektřina výhodně PRO na 3 roky"
    } == {
        (date(2026, 6, 17), date(2026, 12, 31)),
        (date(2027, 1, 1), None),
    }
    assert all(item.contract_kind == "fixed" for item in catalog)
    assert all(item.source_url.startswith("https://www.eon.cz/getmedia/") for item in catalog)
    assert all(item.source_url.endswith(".pdf") for item in catalog)


def test_variant_pro_exact_match_returns_territory_specific_commercial_candidate() -> None:
    sources, eon = load_modules()
    adapter = eon.EonTariffCatalogAdapter(clock=_clock)

    result = __import__("asyncio").run(
        adapter.async_discover(
            _query(
                sources,
                "Variant PRO na 2 roky",
                distributor="cez_distribuce",
            )
        )
    )

    assert len(result) == 1
    candidate = result[0]
    assert candidate.product_name == "Variant PRO na 2 roky"
    assert candidate.match_score == 100
    assert candidate.valid_from == date(2026, 3, 30)
    assert candidate.valid_to is None
    assert candidate.price_scope == sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL
    assert candidate.document.supplier == "eon"
    assert candidate.document.document_date == date(2026, 3, 30)
    assert candidate.document.content_type == "application/pdf"
    assert "distribucni--uzemi--cez.pdf" in candidate.document.source_url
    assert "exact Czech distribution territory" in candidate.match_reasons[2]
    assert "regulated values are separate" in candidate.match_reasons[-1]


def test_product_matching_is_accent_case_normalized_but_never_fuzzy() -> None:
    sources, eon = load_modules()
    adapter = eon.EonTariffCatalogAdapter(clock=_clock)

    matched = __import__("asyncio").run(
        adapter.async_discover(
            _query(sources, "ELEKTRINA VYHODNE PRO NA 3 ROKY")
        )
    )
    fuzzy = __import__("asyncio").run(
        adapter.async_discover(_query(sources, "Variant PRO"))
    )

    assert [item.product_name for item in matched] == [
        "Elektřina výhodně PRO na 3 roky"
    ]
    assert fuzzy == ()


def test_distribution_territory_is_part_of_fail_closed_match() -> None:
    sources, eon = load_modules()
    adapter = eon.EonTariffCatalogAdapter(clock=_clock)

    egd = __import__("asyncio").run(
        adapter.async_discover(
            _query(sources, "Variant PRO na 2 roky", distributor="eg_d")
        )
    )
    pre = __import__("asyncio").run(
        adapter.async_discover(
            _query(sources, "Variant PRO na 2 roky", distributor="pre_distribuce")
        )
    )

    assert len(egd) == 1
    assert len(pre) == 1
    assert "distribucni--uzemi--eg.d.pdf" in egd[0].document.source_url
    assert "distribucni--uzemi--pre.pdf" in pre[0].document.source_url
    assert egd[0].document.source_url != pre[0].document.source_url


def test_fixed_contract_kind_is_required() -> None:
    sources, eon = load_modules()
    adapter = eon.EonTariffCatalogAdapter(clock=_clock)

    fixed = __import__("asyncio").run(
        adapter.async_discover(
            _query(sources, "Variant PRO na 2 roky", contract_kind="fixed")
        )
    )
    indefinite = __import__("asyncio").run(
        adapter.async_discover(
            _query(sources, "Variant PRO na 2 roky", contract_kind="indefinite")
        )
    )

    assert len(fixed) == 1
    assert indefinite == ()


def test_catalog_respects_commercial_price_start_without_regulated_year_cutoff() -> None:
    sources, eon = load_modules()
    adapter = eon.EonTariffCatalogAdapter(clock=_clock)

    before = __import__("asyncio").run(
        adapter.async_discover(
            _query(
                sources,
                "Variant PRO na 2 roky",
                valid_on=date(2026, 3, 29),
            )
        )
    )
    first_day = __import__("asyncio").run(
        adapter.async_discover(
            _query(
                sources,
                "Variant PRO na 2 roky",
                valid_on=date(2026, 3, 30),
            )
        )
    )
    next_year = __import__("asyncio").run(
        adapter.async_discover(
            _query(
                sources,
                "Variant PRO na 2 roky",
                valid_on=date(2027, 1, 1),
            )
        )
    )

    assert before == ()
    assert len(first_day) == 1
    assert len(next_year) == 1
    assert next_year[0].price_scope == sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL


def test_second_verified_product_uses_its_own_official_valid_from() -> None:
    sources, eon = load_modules()
    adapter = eon.EonTariffCatalogAdapter(clock=_clock)

    before = __import__("asyncio").run(
        adapter.async_discover(
            _query(
                sources,
                "Elektřina výhodně PRO na 3 roky",
                valid_on=date(2026, 6, 16),
            )
        )
    )
    active = __import__("asyncio").run(
        adapter.async_discover(
            _query(
                sources,
                "Elektřina výhodně PRO na 3 roky",
                valid_on=date(2026, 6, 17),
            )
        )
    )

    assert before == ()
    assert len(active) == 1
    assert active[0].valid_from == date(2026, 6, 17)
    assert active[0].price_scope == sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL


def test_registry_verifies_eon_output_against_eon_official_domain() -> None:
    sources, eon = load_modules()
    registry = sources.TariffAdapterRegistry()
    registry.register(eon.EonTariffCatalogAdapter(clock=_clock))

    candidates = __import__("asyncio").run(
        registry.async_discover_verified(
            _query(
                sources,
                "Elektřina výhodně PRO na 3 roky",
                distributor="pre_distribuce",
            )
        )
    )

    assert len(candidates) == 1
    assert candidates[0].document.source_url.startswith(
        "https://www.eon.cz/getmedia/"
    )
    assert "distribucni--uzemi--pre.pdf" in candidates[0].document.source_url


def test_wrong_supplier_query_returns_no_eon_candidate() -> None:
    sources, eon = load_modules()
    adapter = eon.EonTariffCatalogAdapter(clock=_clock)

    assert __import__("asyncio").run(
        adapter.async_discover(
            _query(sources, "Variant PRO na 2 roky", supplier="cez")
        )
    ) == ()


def test_naive_clock_fails_closed_before_candidate_creation() -> None:
    sources, eon = load_modules()
    adapter = eon.EonTariffCatalogAdapter(
        clock=lambda: datetime(2026, 8, 14, 12, 30)
    )

    try:
        __import__("asyncio").run(
            adapter.async_discover(_query(sources, "Variant PRO na 2 roky"))
        )
    except ValueError as err:
        assert "timezone-aware" in str(err)
    else:
        raise AssertionError("Naive discovery clock must fail closed")
