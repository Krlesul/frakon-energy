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
        "custom_components.frakon_energy.providers.pre_tariffs",
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

    source_spec = importlib.util.spec_from_file_location(
        "custom_components.frakon_energy.tariff_sources",
        Path("custom_components/frakon_energy/tariff_sources.py"),
    )
    sources = importlib.util.module_from_spec(source_spec)
    sys.modules[source_spec.name] = sources
    source_spec.loader.exec_module(sources)

    pre_spec = importlib.util.spec_from_file_location(
        "custom_components.frakon_energy.providers.pre_tariffs",
        Path("custom_components/frakon_energy/providers/pre_tariffs.py"),
    )
    pre = importlib.util.module_from_spec(pre_spec)
    sys.modules[pre_spec.name] = pre
    pre_spec.loader.exec_module(pre)
    return sources, pre


def _query(
    sources,
    product: str,
    *,
    distributor: str = "pre_distribuce",
    contract_kind: str = "indefinite",
    valid_on: date = date(2026, 8, 14),
):
    return sources.TariffSourceQuery(
        supplier="pre",
        product_name=product,
        distributor=distributor,
        contract_kind=contract_kind,
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_on=valid_on,
    )


def _clock():
    return datetime(2026, 8, 14, 13, 30, tzinfo=timezone.utc)


def test_catalog_has_exact_three_products_for_all_three_distribution_territories() -> None:
    _, pre = load_modules()

    assert len(pre.PRE_CURRENT_COMMERCIAL_CATALOG) == 9
    assert {item.product_name for item in pre.PRE_CURRENT_COMMERCIAL_CATALOG} == {
        "PRE PROUD NEFIX",
        "PRE PROUD FAVORIT 2",
        "PRE PROUD FAVORIT 3",
    }
    for product in (
        "PRE PROUD NEFIX",
        "PRE PROUD FAVORIT 2",
        "PRE PROUD FAVORIT 3",
    ):
        assert {
            item.distributor
            for item in pre.PRE_CURRENT_COMMERCIAL_CATALOG
            if item.product_name == product
        } == {"pre_distribuce", "eg_d", "cez_distribuce"}

    assert all(
        item.source_url.startswith(
            "https://www.pre.cz/cs/linky/dokumenty-ke-stazeni/cenik/elektrina/"
        )
        for item in pre.PRE_CURRENT_COMMERCIAL_CATALOG
    )


def test_nefix_is_indefinite_and_favorit_products_are_fixed() -> None:
    _, pre = load_modules()

    for item in pre.PRE_CURRENT_COMMERCIAL_CATALOG:
        if item.product_name == "PRE PROUD NEFIX":
            assert item.contract_kind == "indefinite"
            assert item.valid_from == date(2026, 1, 1)
        else:
            assert item.contract_kind == "fixed"
            assert item.valid_from == date(2026, 8, 1)


def test_exact_product_and_alias_return_supplier_commercial_candidate() -> None:
    sources, pre = load_modules()
    adapter = pre.PreTariffCatalogAdapter(clock=_clock)

    canonical = __import__("asyncio").run(
        adapter.async_discover(_query(sources, "PRE PROUD NEFIX"))
    )
    alias = __import__("asyncio").run(
        adapter.async_discover(_query(sources, "PROUD NEFIX"))
    )

    assert len(canonical) == 1
    assert canonical[0].match_score == 100
    assert len(alias) == 1
    assert alias[0].match_score == 98
    for candidate in (canonical[0], alias[0]):
        assert candidate.product_name == "PRE PROUD NEFIX"
        assert candidate.price_scope == sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL
        assert candidate.document.supplier == "pre"
        assert candidate.document.content_type == "application/pdf"
        assert "exact PRE contract kind" in candidate.match_reasons
        assert "exact PRE distribution territory" in candidate.match_reasons
        assert "regulated values are separate" in candidate.match_reasons[-1]


def test_distributor_must_match_the_selected_official_pdf() -> None:
    sources, pre = load_modules()
    adapter = pre.PreTariffCatalogAdapter(clock=_clock)

    pre_result = __import__("asyncio").run(
        adapter.async_discover(_query(sources, "PRE PROUD NEFIX"))
    )
    egd_result = __import__("asyncio").run(
        adapter.async_discover(
            _query(sources, "PRE PROUD NEFIX", distributor="eg_d")
        )
    )
    cez_result = __import__("asyncio").run(
        adapter.async_discover(
            _query(sources, "PRE PROUD NEFIX", distributor="cez_distribuce")
        )
    )

    assert pre_result[0].document.source_url.endswith("/pre/moo/pre-proud-nefix/")
    assert egd_result[0].document.source_url.endswith("/egd/moo/pre-proud-nefix/")
    assert cez_result[0].document.source_url.endswith("/cez/moo/pre-proud-nefix/")


def test_contract_kind_is_fail_closed() -> None:
    sources, pre = load_modules()
    adapter = pre.PreTariffCatalogAdapter(clock=_clock)

    wrong_nefix = __import__("asyncio").run(
        adapter.async_discover(
            _query(sources, "PRE PROUD NEFIX", contract_kind="fixed")
        )
    )
    fixed = __import__("asyncio").run(
        adapter.async_discover(
            _query(sources, "PRE PROUD FAVORIT 2", contract_kind="fixed")
        )
    )
    wrong_favorit = __import__("asyncio").run(
        adapter.async_discover(
            _query(sources, "PRE PROUD FAVORIT 2", contract_kind="indefinite")
        )
    )

    assert wrong_nefix == ()
    assert [item.product_name for item in fixed] == ["PRE PROUD FAVORIT 2"]
    assert wrong_favorit == ()


def test_august_favorit_catalog_never_applies_before_validity() -> None:
    sources, pre = load_modules()
    adapter = pre.PreTariffCatalogAdapter(clock=_clock)

    before = __import__("asyncio").run(
        adapter.async_discover(
            _query(
                sources,
                "PRE PROUD FAVORIT 3",
                contract_kind="fixed",
                valid_on=date(2026, 7, 31),
            )
        )
    )
    on_start = __import__("asyncio").run(
        adapter.async_discover(
            _query(
                sources,
                "PRE PROUD FAVORIT 3",
                contract_kind="fixed",
                valid_on=date(2026, 8, 1),
            )
        )
    )

    assert before == ()
    assert len(on_start) == 1


def test_registry_revalidates_pre_candidate_against_official_domain() -> None:
    sources, pre = load_modules()
    registry = sources.TariffAdapterRegistry()
    registry.register(pre.PreTariffCatalogAdapter(clock=_clock))

    candidates = __import__("asyncio").run(
        registry.async_discover_verified(
            _query(
                sources,
                "PRE PROUD FAVORIT 3",
                distributor="cez_distribuce",
                contract_kind="fixed",
            )
        )
    )

    assert len(candidates) == 1
    assert candidates[0].document.source_url == (
        "https://www.pre.cz/cs/linky/dokumenty-ke-stazeni/cenik/elektrina/"
        "cez/moo/pre-proud-favorit-3/"
    )


def test_unknown_product_and_wrong_supplier_have_no_fallback() -> None:
    sources, pre = load_modules()
    adapter = pre.PreTariffCatalogAdapter(clock=_clock)

    unknown = __import__("asyncio").run(
        adapter.async_discover(_query(sources, "PRE PROUD FAVORIT"))
    )
    wrong_supplier_query = sources.TariffSourceQuery(
        supplier="eon",
        product_name="PRE PROUD NEFIX",
        distributor="pre_distribuce",
        contract_kind="indefinite",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_on=date(2026, 8, 14),
    )
    wrong_supplier = __import__("asyncio").run(
        adapter.async_discover(wrong_supplier_query)
    )

    assert unknown == ()
    assert wrong_supplier == ()
