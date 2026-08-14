from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types


def load_modules():
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.pricing",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components.frakon_energy.cz_regulated_sources",
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

    pricing = load(
        "custom_components.frakon_energy.pricing",
        "custom_components/frakon_energy/pricing.py",
    )
    tariff_sources = load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    provenance = load(
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components/frakon_energy/tariff_provenance.py",
    )
    regulated_pricing = load(
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components/frakon_energy/regulated_pricing.py",
    )
    cz = load(
        "custom_components.frakon_energy.cz_regulated_sources",
        "custom_components/frakon_energy/cz_regulated_sources.py",
    )
    return pricing, tariff_sources, provenance, regulated_pricing, cz


def _eru(cz):
    return cz.RegulatedPriceSource(
        authority=cz.RegulatedAuthority.ERU,
        document_id="Cenový výměr 14/2025",
        source_url="https://eru.gov.cz/energeticky-regulacni-vestnik-182025",
        valid_from=date(2026, 1, 1),
    )


def _ote(cz):
    return cz.RegulatedPriceSource(
        authority=cz.RegulatedAuthority.OTE,
        document_id="OTE 2026",
        source_url="https://www.ote-cr.cz/cs/registrace-a-smlouvy/smluvni-vztahy-elektrina/ceny-za-sluzby-ote",
        valid_from=date(2026, 1, 1),
    )


def _inputs(cz):
    return cz.CzechRegulatedTariffInputs(
        distributor="cez_distribuce",
        distribution_tariff="d25D",
        breaker_code="3x25A",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        distribution_vt_czk_per_kwh=Decimal("1.000"),
        distribution_nt_czk_per_kwh=Decimal("0.500"),
        breaker_monthly_czk=Decimal("200"),
        system_services_czk_per_kwh=Decimal("0.100"),
        electricity_tax_czk_per_kwh=Decimal("0.02830"),
        sources=(_eru(cz), _ote(cz)),
    )


def test_czech_inputs_convert_to_single_canonical_regulated_bundle() -> None:
    pricing, sources, _, regulated_pricing, cz = load_modules()
    bundle = _inputs(cz).to_bundle(confirmed=True)

    assert isinstance(bundle, regulated_pricing.RegulatedTariffBundle)
    assert bundle.distribution_tariff == "D25d"
    assert bundle.breaker_code == "3x25A"
    assert bundle.price_scope == sources.PRICE_SCOPE_REGULATED
    assert bundle.all_in_ready is False
    assert bundle.confirmed is True
    assert [item.kind for item in bundle.variable_components] == [
        pricing.PriceComponentKind.DISTRIBUTION,
        pricing.PriceComponentKind.SYSTEM_SERVICES,
        pricing.PriceComponentKind.POZE,
        pricing.PriceComponentKind.ELECTRICITY_TAX,
    ]
    assert [item.kind for item in bundle.fixed_components] == [
        pricing.PriceComponentKind.BREAKER_FIXED,
        pricing.PriceComponentKind.OTHER_FIXED,
    ]
    assert bundle.variable_components[2].high_rate_czk_per_kwh == Decimal("0")
    assert bundle.fixed_components[1].name == regulated_pricing.NON_NETWORK_INFRASTRUCTURE_COMPONENT_NAME
    assert bundle.fixed_components[1].monthly_czk == Decimal("12.87")
    assert all(item.includes_vat is False for item in bundle.variable_components)
    assert all(item.includes_vat is False for item in bundle.fixed_components)


def test_czech_sources_convert_to_regulated_multi_source_evidence() -> None:
    _, sources, provenance, _, cz = load_modules()
    evidence = _inputs(cz).regulated_evidence()

    assert len(evidence) == 2
    assert all(isinstance(item, provenance.PriceEvidence) for item in evidence)
    assert {item.scope for item in evidence} == {sources.PRICE_SCOPE_REGULATED}
    assert {item.source_name for item in evidence} == {
        "Energetický regulační úřad",
        "OTE",
    }


def test_czech_inputs_require_eru_provenance_and_full_period_coverage() -> None:
    _, _, _, _, cz = load_modules()

    try:
        cz.CzechRegulatedTariffInputs(
            distributor="cez_distribuce",
            distribution_tariff="D25d",
            breaker_code="3x25A",
            valid_from=date(2026, 1, 1),
            distribution_vt_czk_per_kwh=Decimal("1"),
            distribution_nt_czk_per_kwh=Decimal("1"),
            breaker_monthly_czk=Decimal("1"),
            system_services_czk_per_kwh=Decimal("1"),
            electricity_tax_czk_per_kwh=Decimal("0.02"),
            sources=(_ote(cz),),
        )
    except ValueError as err:
        assert "ERÚ source" in str(err)
    else:
        raise AssertionError("Czech regulated inputs without ERÚ must be rejected")

    short_eru = cz.RegulatedPriceSource(
        authority=cz.RegulatedAuthority.ERU,
        document_id="Short source",
        source_url="https://eru.gov.cz/example",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 6, 30),
    )
    try:
        cz.CzechRegulatedTariffInputs(
            distributor="cez_distribuce",
            distribution_tariff="D25d",
            breaker_code="3x25A",
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
            distribution_vt_czk_per_kwh=Decimal("1"),
            distribution_nt_czk_per_kwh=Decimal("1"),
            breaker_monthly_czk=Decimal("1"),
            system_services_czk_per_kwh=Decimal("1"),
            electricity_tax_czk_per_kwh=Decimal("0.02"),
            sources=(short_eru,),
        )
    except ValueError as err:
        assert "cover valid_to" in str(err)
    else:
        raise AssertionError("Source that expires early must not back a longer tariff period")


def test_regulated_source_rejects_spoofed_or_nonstandard_authority_urls() -> None:
    _, _, _, _, cz = load_modules()
    for url in (
        "http://eru.gov.cz/cenik",
        "https://eru.gov.cz.evil.example/cenik",
        "https://user:pass@eru.gov.cz/cenik",
        "https://eru.gov.cz:8443/cenik",
    ):
        try:
            cz.RegulatedPriceSource(
                authority=cz.RegulatedAuthority.ERU,
                document_id="test",
                source_url=url,
                valid_from=date(2026, 1, 1),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Unsafe authority URL must be rejected")


def test_regulated_bundle_rejects_arbitrary_other_fixed() -> None:
    pricing, _, _, regulated_pricing, _ = load_modules()
    distribution = pricing.VariablePriceComponent(
        pricing.PriceComponentKind.DISTRIBUTION,
        "Distribuce",
        Decimal("1"),
        Decimal("0.5"),
        includes_vat=False,
    )
    breaker = pricing.FixedPriceComponent(
        pricing.PriceComponentKind.BREAKER_FIXED,
        "Jistič",
        Decimal("200"),
        includes_vat=False,
    )
    arbitrary = pricing.FixedPriceComponent(
        pricing.PriceComponentKind.OTHER_FIXED,
        "Libovolný jiný regulovaný poplatek",
        Decimal("1"),
        includes_vat=False,
    )

    try:
        regulated_pricing.RegulatedTariffBundle(
            distributor="cez_distribuce",
            distribution_tariff="D25d",
            breaker_code="3x25A",
            valid_from=date(2026, 1, 1),
            variable_components=(distribution,),
            fixed_components=(breaker, arbitrary),
            source_url="https://eru.gov.cz/energeticky-regulacni-vestnik-182025",
        )
    except ValueError as err:
        assert "unsupported kind" in str(err)
    else:
        raise AssertionError("Arbitrary OTHER_FIXED must not enter regulated pricing")


def test_2026_universal_constants_are_source_anchored_but_baseline_is_not_current_claim() -> None:
    _, _, _, _, cz = load_modules()
    baseline = cz.official_2026_baseline_sources()

    assert cz.POZE_2026_CZK_PER_KWH == Decimal("0")
    assert cz.OTE_NON_NETWORK_2026_CZK_PER_MONTH == Decimal("12.87")
    assert {item.authority for item in baseline} == {
        cz.RegulatedAuthority.ERU,
        cz.RegulatedAuthority.OTE,
    }
    assert all(item.valid_from == date(2026, 1, 1) for item in baseline)
    assert any("14/2025" in item.document_id for item in baseline)
    assert any("13/2025" in item.document_id for item in baseline)
    assert any("15/2025" in item.document_id for item in baseline)
    assert cz.ERU_1_2026_AMENDMENT_PAGE.endswith("energeticky-regulacni-vestnik-22026")
