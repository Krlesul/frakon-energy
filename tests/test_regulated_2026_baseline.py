from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types


def load_modules():
    for name in (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.pricing",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components.frakon_energy.regulated_2026_baseline",
    ):
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

    pricing = load(
        "custom_components.frakon_energy.pricing",
        "custom_components/frakon_energy/pricing.py",
    )
    sources = load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    provenance = load(
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components/frakon_energy/tariff_provenance.py",
    )
    regulated = load(
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components/frakon_energy/regulated_pricing.py",
    )
    baseline = load(
        "custom_components.frakon_energy.regulated_2026_baseline",
        "custom_components/frakon_energy/regulated_2026_baseline.py",
    )
    return pricing, sources, provenance, regulated, baseline


def test_universal_2026_baseline_preserves_official_net_values_and_evidence() -> None:
    pricing, sources, provenance, _, baseline = load_modules()
    result = baseline.universal_regulated_2026_baseline()

    assert result.valid_from == date(2026, 1, 1)
    assert result.price_scope == sources.PRICE_SCOPE_REGULATED
    assert result.all_in_ready is False
    assert baseline.POZE_2026_CZK_PER_KWH == Decimal("0")
    assert baseline.OTE_NON_NETWORK_2026_CZK_PER_MONTH == Decimal("12.87")

    poze = result.variable_components[0]
    assert poze.kind == pricing.PriceComponentKind.POZE
    assert poze.high_rate_czk_per_kwh == Decimal("0")
    assert poze.low_rate_czk_per_kwh == Decimal("0")
    assert poze.includes_vat is False

    non_network = result.fixed_components[0]
    assert non_network.kind == pricing.PriceComponentKind.OTHER_FIXED
    assert non_network.name == "Provoz nesíťové infrastruktury"
    assert non_network.monthly_czk == Decimal("12.87")
    assert non_network.includes_vat is False
    assert non_network.gross_monthly_czk == Decimal("15.5727")

    assert len(result.evidence) == 2
    assert all(item.confirmed for item in result.evidence)
    assert all(item.scope == sources.PRICE_SCOPE_REGULATED for item in result.evidence)
    assert {item.source_name for item in result.evidence} == {
        "Energetický regulační úřad",
        "OTE, a.s.",
    }
    assert all(
        item.source_type == provenance.PriceSourceType.OFFICIAL_PRICE_LIST
        for item in result.evidence
    )


def test_regulated_bundle_accepts_only_explicit_non_network_other_fixed() -> None:
    pricing, _, _, regulated, baseline = load_modules()
    universal = baseline.universal_regulated_2026_baseline()
    distribution = pricing.VariablePriceComponent(
        pricing.PriceComponentKind.DISTRIBUTION,
        "Distribuce",
        Decimal("1.00"),
        Decimal("0.50"),
        includes_vat=False,
    )
    system_services = pricing.VariablePriceComponent(
        pricing.PriceComponentKind.SYSTEM_SERVICES,
        "Systémové služby",
        Decimal("0.10"),
        Decimal("0.10"),
        includes_vat=False,
    )
    breaker = pricing.FixedPriceComponent(
        pricing.PriceComponentKind.BREAKER_FIXED,
        "Jistič",
        Decimal("200"),
        includes_vat=False,
    )

    bundle = regulated.RegulatedTariffBundle(
        distributor="cez_distribuce",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_from=date(2026, 1, 1),
        variable_components=(distribution, system_services, *universal.variable_components),
        fixed_components=(breaker, *universal.fixed_components),
        source_url="https://eru.gov.cz/energeticky-regulacni-vestnik-182025",
        confirmed=False,
    )
    assert bundle.all_in_ready is False
    assert any(
        item.name == regulated.NON_NETWORK_INFRASTRUCTURE_COMPONENT_NAME
        for item in bundle.fixed_components
    )

    arbitrary_other = pricing.FixedPriceComponent(
        pricing.PriceComponentKind.OTHER_FIXED,
        "Libovolný jiný poplatek",
        Decimal("1"),
        includes_vat=False,
    )
    try:
        regulated.RegulatedTariffBundle(
            distributor="cez_distribuce",
            distribution_tariff="D25d",
            breaker_code="3x25A",
            valid_from=date(2026, 1, 1),
            variable_components=(distribution,),
            fixed_components=(breaker, arbitrary_other),
            source_url="https://eru.gov.cz/energeticky-regulacni-vestnik-182025",
        )
    except ValueError as err:
        assert "unsupported kind" in str(err)
    else:
        raise AssertionError("Arbitrary OTHER_FIXED must not enter regulated pricing")
