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
        "custom_components.frakon_energy.regulated_tariff",
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
    regulated = load(
        "custom_components.frakon_energy.regulated_tariff",
        "custom_components/frakon_energy/regulated_tariff.py",
    )
    return pricing, sources, regulated


def _eru_source(regulated):
    return regulated.RegulatedPriceSource(
        authority=regulated.RegulatedAuthority.ERU,
        document_id="Cenový výměr 14/2025",
        source_url="https://eru.gov.cz/energeticky-regulacni-vestnik-182025",
        valid_from=date(2026, 1, 1),
    )


def test_regulated_bundle_maps_net_values_to_partial_pricing_components() -> None:
    pricing, sources, regulated = load_modules()
    bundle = regulated.RegulatedTariffComponents(
        distributor="cez_distribuce",
        distribution_tariff="d25D",
        breaker_code="3x25A",
        valid_from=date(2026, 1, 1),
        distribution_vt_czk_per_kwh=Decimal("1.000"),
        distribution_nt_czk_per_kwh=Decimal("0.500"),
        breaker_monthly_czk=Decimal("200"),
        system_services_czk_per_kwh=Decimal("0.100"),
        poze_czk_per_kwh=regulated.POZE_2026_CZK_PER_KWH,
        non_network_monthly_czk=regulated.OTE_NON_NETWORK_2026_CZK_PER_MONTH,
        sources=(_eru_source(regulated),),
    )

    assert bundle.distribution_tariff == "D25d"
    assert bundle.price_scope == sources.PRICE_SCOPE_REGULATED
    assert bundle.all_in_ready is False
    assert [item.kind for item in bundle.variable_components] == [
        pricing.PriceComponentKind.DISTRIBUTION,
        pricing.PriceComponentKind.SYSTEM_SERVICES,
        pricing.PriceComponentKind.POZE,
    ]
    assert bundle.variable_components[0].includes_vat is False
    assert bundle.variable_components[2].high_rate_czk_per_kwh == Decimal("0")
    assert [item.kind for item in bundle.fixed_components] == [
        pricing.PriceComponentKind.BREAKER_FIXED,
        pricing.PriceComponentKind.OTHER_FIXED,
    ]
    assert bundle.fixed_components[1].monthly_czk == Decimal("12.87")
    assert all(item.includes_vat is False for item in bundle.fixed_components)


def test_regulated_bundle_requires_official_eru_provenance() -> None:
    _, _, regulated = load_modules()
    ote = regulated.RegulatedPriceSource(
        authority=regulated.RegulatedAuthority.OTE,
        document_id="OTE 2026",
        source_url="https://www.ote-cr.cz/cs/registrace-a-smlouvy/smluvni-vztahy-elektrina/ceny-za-sluzby-ote",
        valid_from=date(2026, 1, 1),
    )

    try:
        regulated.RegulatedTariffComponents(
            distributor="cez_distribuce",
            distribution_tariff="D25d",
            breaker_code="3x25A",
            valid_from=date(2026, 1, 1),
            distribution_vt_czk_per_kwh=Decimal("1"),
            distribution_nt_czk_per_kwh=Decimal("1"),
            breaker_monthly_czk=Decimal("1"),
            system_services_czk_per_kwh=Decimal("1"),
            poze_czk_per_kwh=Decimal("0"),
            non_network_monthly_czk=Decimal("12.87"),
            sources=(ote,),
        )
    except ValueError as err:
        assert "ERÚ source" in str(err)
    else:
        raise AssertionError("Regulated bundle without ERÚ provenance must be rejected")


def test_regulated_source_rejects_spoofed_or_nonstandard_urls() -> None:
    _, _, regulated = load_modules()
    for url in (
        "http://eru.gov.cz/cenik",
        "https://eru.gov.cz.evil.example/cenik",
        "https://user:pass@eru.gov.cz/cenik",
        "https://eru.gov.cz:8443/cenik",
    ):
        try:
            regulated.RegulatedPriceSource(
                authority=regulated.RegulatedAuthority.ERU,
                document_id="test",
                source_url=url,
                valid_from=date(2026, 1, 1),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Unsafe regulated source URL must be rejected")


def test_official_2026_baseline_sources_are_regulator_owned_and_complete() -> None:
    _, _, regulated = load_modules()
    sources = regulated.official_2026_baseline_sources()

    assert {item.authority for item in sources} == {
        regulated.RegulatedAuthority.ERU,
        regulated.RegulatedAuthority.OTE,
    }
    assert all(item.valid_from == date(2026, 1, 1) for item in sources)
    assert any("14/2025" in item.document_id for item in sources)
    assert any("13/2025" in item.document_id for item in sources)
    assert any("15/2025" in item.document_id for item in sources)
    assert any(item.authority == regulated.RegulatedAuthority.OTE for item in sources)
    assert regulated.POZE_2026_CZK_PER_KWH == Decimal("0")
    assert regulated.OTE_NON_NETWORK_2026_CZK_PER_MONTH == Decimal("12.87")
