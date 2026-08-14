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
        "custom_components.frakon_energy.tariff_assembly",
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
    cz = load(
        "custom_components.frakon_energy.cz_regulated_sources",
        "custom_components/frakon_energy/cz_regulated_sources.py",
    )
    assembly = load(
        "custom_components.frakon_energy.tariff_assembly",
        "custom_components/frakon_energy/tariff_assembly.py",
    )
    return pricing, sources, provenance, regulated, cz, assembly


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


def _regulated_inputs(cz):
    return cz.CzechRegulatedTariffInputs(
        distributor="cez_distribuce",
        distribution_tariff="D25d",
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


def _supplier_evidence(sources, provenance):
    return provenance.PriceEvidence(
        scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
        source_name="ČEZ Prodej",
        document_name="Basic 2026",
        source_url="https://www.cez.cz/file/edee/basic-2026.pdf",
        valid_from=date(2026, 1, 1),
        checksum="a" * 64,
    )


def _provenance(sources, provenance, cz):
    inputs = _regulated_inputs(cz)
    return provenance.MultiSourceTariffProvenance(
        (_supplier_evidence(sources, provenance), *inputs.regulated_evidence())
    )


def _commodity(pricing):
    return pricing.VariablePriceComponent(
        pricing.PriceComponentKind.COMMODITY,
        "ČEZ commodity",
        Decimal("3.960"),
        Decimal("3.700"),
    )


def _supplier_fixed(pricing):
    return pricing.FixedPriceComponent(
        pricing.PriceComponentKind.SUPPLIER_FIXED,
        "ČEZ stálá platba",
        Decimal("130.68"),
    )


def test_all_in_assembly_combines_complete_czech_pipeline() -> None:
    pricing, sources, provenance, _, cz, assembly = load_modules()
    regulated = _regulated_inputs(cz).to_bundle(confirmed=True)
    result = assembly.assemble_all_in_tariff(
        supplier="ČEZ",
        product_name="Basic",
        distribution_tariff="d25D",
        breaker_code="3x25A",
        commercial_valid_from=date(2026, 1, 1),
        commodity=_commodity(pricing),
        supplier_fixed=_supplier_fixed(pricing),
        regulated=regulated,
        provenance=_provenance(sources, provenance, cz),
    )

    assert result.price_scope == sources.PRICE_SCOPE_ALL_IN
    assert result.all_in_ready is True
    assert result.distribution_tariff == "D25d"
    assert result.breaker_code == "3x25A"
    assert result.valid_from == date(2026, 1, 1)
    assert result.valid_to == date(2026, 12, 31)
    assert result.all_in_vt_czk_kwh == Decimal("5.325243")
    assert result.all_in_nt_czk_kwh == Decimal("4.460243")
    assert result.fixed_monthly_total_czk == Decimal("388.2527")
    assert [item.kind for item in result.variable_components] == [
        pricing.PriceComponentKind.COMMODITY,
        pricing.PriceComponentKind.DISTRIBUTION,
        pricing.PriceComponentKind.SYSTEM_SERVICES,
        pricing.PriceComponentKind.POZE,
        pricing.PriceComponentKind.ELECTRICITY_TAX,
    ]
    assert [item.kind for item in result.fixed_components] == [
        pricing.PriceComponentKind.SUPPLIER_FIXED,
        pricing.PriceComponentKind.BREAKER_FIXED,
        pricing.PriceComponentKind.OTHER_FIXED,
    ]


def test_all_in_assembly_rejects_incomplete_regulated_bundle() -> None:
    pricing, sources, provenance, regulated_module, cz, assembly = load_modules()
    incomplete = regulated_module.RegulatedTariffBundle(
        distributor="cez_distribuce",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        variable_components=(
            pricing.VariablePriceComponent(
                pricing.PriceComponentKind.DISTRIBUTION,
                "Distribuce",
                Decimal("1"),
                Decimal("0.5"),
            ),
            pricing.VariablePriceComponent(
                pricing.PriceComponentKind.POZE,
                "POZE",
                Decimal("0"),
                Decimal("0"),
            ),
        ),
        fixed_components=(
            pricing.FixedPriceComponent(
                pricing.PriceComponentKind.BREAKER_FIXED,
                "Jistič",
                Decimal("200"),
            ),
        ),
        source_url=_eru(cz).source_url,
        confirmed=True,
    )

    try:
        assembly.assemble_all_in_tariff(
            supplier="ČEZ",
            product_name="Basic",
            distribution_tariff="D25d",
            breaker_code="3x25A",
            commercial_valid_from=date(2026, 1, 1),
            commodity=_commodity(pricing),
            supplier_fixed=_supplier_fixed(pricing),
            regulated=incomplete,
            provenance=_provenance(sources, provenance, cz),
        )
    except ValueError as err:
        assert "system_services" in str(err)
    else:
        raise AssertionError("Incomplete regulated data must not become all-in ready")


def test_all_in_assembly_requires_confirmed_regulated_bundle() -> None:
    pricing, sources, provenance, _, cz, assembly = load_modules()
    try:
        assembly.assemble_all_in_tariff(
            supplier="ČEZ",
            product_name="Basic",
            distribution_tariff="D25d",
            breaker_code="3x25A",
            commercial_valid_from=date(2026, 1, 1),
            commodity=_commodity(pricing),
            supplier_fixed=_supplier_fixed(pricing),
            regulated=_regulated_inputs(cz).to_bundle(confirmed=False),
            provenance=_provenance(sources, provenance, cz),
        )
    except ValueError as err:
        assert "must be confirmed" in str(err)
    else:
        raise AssertionError("Unconfirmed regulated data must not become all-in ready")


def test_all_in_assembly_requires_matching_regulated_provenance() -> None:
    pricing, sources, provenance, _, cz, assembly = load_modules()
    regulated = _regulated_inputs(cz).to_bundle(confirmed=True)
    other_regulated = provenance.PriceEvidence(
        scope=sources.PRICE_SCOPE_REGULATED,
        source_name="Energetický regulační úřad",
        document_name="Jiný regulovaný dokument",
        source_url="https://eru.gov.cz/jiny-regulovany-dokument",
        valid_from=date(2026, 1, 1),
    )
    wrong_provenance = provenance.MultiSourceTariffProvenance(
        (_supplier_evidence(sources, provenance), other_regulated)
    )

    try:
        assembly.assemble_all_in_tariff(
            supplier="ČEZ",
            product_name="Basic",
            distribution_tariff="D25d",
            breaker_code="3x25A",
            commercial_valid_from=date(2026, 1, 1),
            commodity=_commodity(pricing),
            supplier_fixed=_supplier_fixed(pricing),
            regulated=regulated,
            provenance=wrong_provenance,
        )
    except ValueError as err:
        assert "regulated bundle source URL" in str(err)
    else:
        raise AssertionError("Unrelated regulated provenance must not back the bundle")


def test_all_in_assembly_rejects_tariff_breaker_and_component_kind_mismatch() -> None:
    pricing, sources, provenance, _, cz, assembly = load_modules()
    prov = _provenance(sources, provenance, cz)
    regulated = _regulated_inputs(cz).to_bundle(confirmed=True)

    for tariff, breaker, expected in (
        ("D27d", "3x25A", "distribution tariffs do not match"),
        ("D25d", "3x20A", "breaker does not match"),
    ):
        try:
            assembly.assemble_all_in_tariff(
                supplier="ČEZ",
                product_name="Basic",
                distribution_tariff=tariff,
                breaker_code=breaker,
                commercial_valid_from=date(2026, 1, 1),
                commodity=_commodity(pricing),
                supplier_fixed=_supplier_fixed(pricing),
                regulated=regulated,
                provenance=prov,
            )
        except ValueError as err:
            assert expected in str(err)
        else:
            raise AssertionError("Mismatched customer tariff boundary must be rejected")

    wrong = pricing.VariablePriceComponent(
        pricing.PriceComponentKind.DISTRIBUTION,
        "Wrong commercial component",
        Decimal("3"),
        Decimal("2"),
    )
    try:
        assembly.assemble_all_in_tariff(
            supplier="ČEZ",
            product_name="Basic",
            distribution_tariff="D25d",
            breaker_code="3x25A",
            commercial_valid_from=date(2026, 1, 1),
            commodity=wrong,
            supplier_fixed=_supplier_fixed(pricing),
            regulated=regulated,
            provenance=prov,
        )
    except ValueError as err:
        assert "must be COMMODITY" in str(err)
    else:
        raise AssertionError("Wrong commercial component kind must be rejected")


def test_all_in_assembly_uses_actual_validity_intersection_and_rejects_no_overlap() -> None:
    pricing, sources, provenance, _, cz, assembly = load_modules()
    prov = _provenance(sources, provenance, cz)
    regulated = _regulated_inputs(cz).to_bundle(confirmed=True)

    result = assembly.assemble_all_in_tariff(
        supplier="ČEZ",
        product_name="Basic",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        commercial_valid_from=date(2026, 3, 1),
        commercial_valid_to=date(2026, 10, 31),
        commodity=_commodity(pricing),
        supplier_fixed=_supplier_fixed(pricing),
        regulated=regulated,
        provenance=prov,
    )
    assert result.valid_from == date(2026, 3, 1)
    assert result.valid_to == date(2026, 10, 31)

    try:
        assembly.assemble_all_in_tariff(
            supplier="ČEZ",
            product_name="Basic",
            distribution_tariff="D25d",
            breaker_code="3x25A",
            commercial_valid_from=date(2027, 1, 1),
            commodity=_commodity(pricing),
            supplier_fixed=_supplier_fixed(pricing),
            regulated=regulated,
            provenance=prov,
        )
    except ValueError as err:
        assert "do not overlap" in str(err)
    else:
        raise AssertionError("Non-overlapping all-in sources must be rejected")


def test_direct_all_in_object_cannot_bypass_completeness_gate() -> None:
    pricing, sources, provenance, regulated, cz, assembly = load_modules()
    complete = _regulated_inputs(cz).to_bundle(confirmed=True)
    missing_tax = tuple(
        item
        for item in complete.variable_components
        if item.kind != pricing.PriceComponentKind.ELECTRICITY_TAX
    )

    try:
        assembly.AllInTariffAssembly(
            supplier="ČEZ",
            product_name="Basic",
            distribution_tariff="D25d",
            breaker_code="3x25A",
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
            variable_components=(_commodity(pricing), *missing_tax),
            fixed_components=(_supplier_fixed(pricing), *complete.fixed_components),
            provenance=_provenance(sources, provenance, cz),
        )
    except ValueError as err:
        assert "electricity_tax" in str(err)
    else:
        raise AssertionError("Direct construction must not bypass all-in completeness")
