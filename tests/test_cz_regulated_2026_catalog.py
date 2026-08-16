from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types


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
        "custom_components.frakon_energy.pricing",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components.frakon_energy.cz_regulated_sources",
        "custom_components.frakon_energy.cz_regulated_2026_catalog",
    )
    for name in names:
        sys.modules.pop(name, None)
    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    pricing = _load("custom_components.frakon_energy.pricing", "custom_components/frakon_energy/pricing.py")
    _load("custom_components.frakon_energy.tariff_sources", "custom_components/frakon_energy/tariff_sources.py")
    _load("custom_components.frakon_energy.tariff_provenance", "custom_components/frakon_energy/tariff_provenance.py")
    _load("custom_components.frakon_energy.regulated_pricing", "custom_components/frakon_energy/regulated_pricing.py")
    sources = _load("custom_components.frakon_energy.cz_regulated_sources", "custom_components/frakon_energy/cz_regulated_sources.py")
    catalog = _load("custom_components.frakon_energy.cz_regulated_2026_catalog", "custom_components/frakon_energy/cz_regulated_2026_catalog.py")
    return pricing, sources, catalog


def test_exact_cez_d25d_3x25_august_2026_snapshot() -> None:
    pricing, sources, catalog = load_modules()
    inputs = catalog.official_2026_regulated_inputs(
        distributor="cez_distribuce",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        day=date(2026, 8, 16),
    )
    bundle = inputs.to_bundle(confirmed=False)

    assert inputs.valid_from == date(2026, 6, 1)
    assert inputs.valid_to == date(2026, 12, 31)
    assert inputs.distribution_vt_czk_per_kwh == Decimal("2.25245")
    assert inputs.distribution_nt_czk_per_kwh == Decimal("0.11650")
    assert inputs.breaker_monthly_czk == Decimal("269")
    assert inputs.system_services_czk_per_kwh == Decimal("0.16424")
    assert inputs.poze_czk_per_kwh == Decimal("0")
    assert inputs.non_network_monthly_czk == Decimal("12.87")
    assert inputs.electricity_tax_czk_per_kwh == Decimal("0.02830")
    assert bundle.confirmed is False
    assert bundle.source_url == catalog.ERU_LOW_VOLTAGE_2026_XLSX_URL
    assert bundle.checksum == catalog.ERU_LOW_VOLTAGE_2026_XLSX_SHA256
    assert [item.kind for item in bundle.variable_components] == [
        pricing.PriceComponentKind.DISTRIBUTION,
        pricing.PriceComponentKind.SYSTEM_SERVICES,
        pricing.PriceComponentKind.POZE,
        pricing.PriceComponentKind.ELECTRICITY_TAX,
    ]
    assert {source.authority for source in inputs.sources} == {
        sources.RegulatedAuthority.ERU,
        sources.RegulatedAuthority.OTE,
        sources.RegulatedAuthority.CUSTOMS,
    }
    assert any(source.checksum == catalog.ERU_AMENDMENT_1_2026_PDF_SHA256 for source in inputs.sources)


def test_pre_june_snapshot_omits_later_amendment_but_keeps_same_d25d_values() -> None:
    _, _, catalog = load_modules()
    inputs = catalog.official_2026_regulated_inputs(
        distributor="cez_distribuce",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        day=date(2026, 5, 31),
    )

    assert inputs.valid_from == date(2026, 1, 1)
    assert inputs.valid_to == date(2026, 5, 31)
    assert inputs.distribution_vt_czk_per_kwh == Decimal("2.25245")
    assert inputs.distribution_nt_czk_per_kwh == Decimal("0.11650")
    assert not any(source.source_url == catalog.ERU_AMENDMENT_1_2026_PDF_URL for source in inputs.sources)


def test_d25d_standard_breaker_table_is_exact_for_all_three_distributors() -> None:
    _, _, catalog = load_modules()
    expected = {
        "cez_distribuce": (Decimal("2.25245"), Decimal("0.11650"), Decimal("269")),
        "eg_d": (Decimal("2.24388"), Decimal("0.22430"), Decimal("245")),
        "pre_distribuce": (Decimal("1.65649"), Decimal("0.17520"), Decimal("200")),
    }
    for distributor, values in expected.items():
        inputs = catalog.official_2026_regulated_inputs(
            distributor=distributor,
            distribution_tariff="D25d",
            breaker_code="3x25A",
            day=date(2026, 8, 16),
        )
        assert (
            inputs.distribution_vt_czk_per_kwh,
            inputs.distribution_nt_czk_per_kwh,
            inputs.breaker_monthly_czk,
        ) == values


def test_unsupported_year_tariff_breaker_or_distributor_fail_closed() -> None:
    _, _, catalog = load_modules()
    cases = (
        dict(distributor="cez_distribuce", distribution_tariff="D25d", breaker_code="3x25A", day=date(2027, 1, 1)),
        dict(distributor="cez_distribuce", distribution_tariff="D57d", breaker_code="3x25A", day=date(2026, 8, 16)),
        dict(distributor="unknown", distribution_tariff="D25d", breaker_code="3x25A", day=date(2026, 8, 16)),
        dict(distributor="cez_distribuce", distribution_tariff="D25d", breaker_code="1x32A", day=date(2026, 8, 16)),
    )
    for kwargs in cases:
        try:
            catalog.official_2026_regulated_inputs(**kwargs)
        except LookupError:
            pass
        else:
            raise AssertionError(f"unsupported regulated identity must fail closed: {kwargs}")
