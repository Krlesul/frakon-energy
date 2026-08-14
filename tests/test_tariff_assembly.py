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
    assembly = load(
        "custom_components.frakon_energy.tariff_assembly",
        "custom_components/frakon_energy/tariff_assembly.py",
    )
    return pricing, sources, provenance, regulated, assembly


def _provenance(sources, provenance):
    supplier = provenance.PriceEvidence(
        scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
        source_name="ČEZ Prodej",
        document_name="Basic 2026",
        source_url="https://www.cez.cz/file/edee/basic-2026.pdf",
        valid_from=date(2026, 1, 1),
        checksum="a" * 64,
    )
    regulated = provenance.PriceEvidence(
        scope=sources.PRICE_SCOPE_REGULATED,
        source_name="Regulované ceny",
        document_name="D25d 2026",
        source_url="https://www.example-regulator.cz/d25d-2026.pdf",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        checksum="b" * 64,
    )
    return provenance.MultiSourceTariffProvenance((supplier, regulated))


def _regulated(pricing, regulated, *, confirmed: bool = True, tariff: str = "D25d", breaker: str = "3x25A"):
    return regulated.RegulatedTariffBundle(
        distributor="cez_distribuce",
        distribution_tariff=tariff,
        breaker_code=breaker,
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        variable_components=(
            pricing.VariablePriceComponent(
                pricing.PriceComponentKind.DISTRIBUTION,
                "Distribuce",
                Decimal("1.20"),
                Decimal("0.40"),
            ),
            pricing.VariablePriceComponent(
                pricing.PriceComponentKind.POZE,
                "POZE",
                Decimal("0.60"),
                Decimal("0.60"),
            ),
        ),
        fixed_components=(
            pricing.FixedPriceComponent(
                pricing.PriceComponentKind.BREAKER_FIXED,
                "Jistič 3x25A",
                Decimal("250"),
            ),
        ),
        source_url="https://www.example-regulator.cz/d25d-2026.pdf",
        checksum="b" * 64,
        confirmed=confirmed,
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


def test_all_in_assembly_combines_only_matching_complete_sources() -> None:
    pricing, sources, provenance, regulated, assembly = load_modules()
    result = assembly.assemble_all_in_tariff(
        supplier="ČEZ",
        product_name="Basic",
        distribution_tariff="d25D",
        breaker_code="3x25A",
        commercial_valid_from=date(2026, 1, 1),
        commodity=_commodity(pricing),
        supplier_fixed=_supplier_fixed(pricing),
        regulated=_regulated(pricing, regulated),
        provenance=_provenance(sources, provenance),
    )

    assert result.price_scope == sources.PRICE_SCOPE_ALL_IN
    assert result.all_in_ready is True
    assert result.distribution_tariff == "D25d"
    assert result.breaker_code == "3x25A"
    assert result.valid_from == date(2026, 1, 1)
    assert result.valid_to == date(2026, 12, 31)
    assert result.all_in_vt_czk_kwh == Decimal("5.760")
    assert result.all_in_nt_czk_kwh == Decimal("4.700")
    assert result.fixed_monthly_total_czk == Decimal("380.68")
    assert [item.kind for item in result.variable_components] == [
        pricing.PriceComponentKind.COMMODITY,
        pricing.PriceComponentKind.DISTRIBUTION,
        pricing.PriceComponentKind.POZE,
    ]
    assert [item.kind for item in result.fixed_components] == [
        pricing.PriceComponentKind.SUPPLIER_FIXED,
        pricing.PriceComponentKind.BREAKER_FIXED,
    ]


def test_all_in_assembly_requires_confirmed_regulated_bundle() -> None:
    pricing, sources, provenance, regulated, assembly = load_modules()
    try:
        assembly.assemble_all_in_tariff(
            supplier="ČEZ",
            product_name="Basic",
            distribution_tariff="D25d",
            breaker_code="3x25A",
            commercial_valid_from=date(2026, 1, 1),
            commodity=_commodity(pricing),
            supplier_fixed=_supplier_fixed(pricing),
            regulated=_regulated(pricing, regulated, confirmed=False),
            provenance=_provenance(sources, provenance),
        )
    except ValueError as err:
        assert "must be confirmed" in str(err)
    else:
        raise AssertionError("Unconfirmed regulated data must not become all-in ready")


def test_all_in_assembly_rejects_tariff_breaker_and_component_kind_mismatch() -> None:
    pricing, sources, provenance, regulated, assembly = load_modules()
    prov = _provenance(sources, provenance)

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
                regulated=_regulated(pricing, regulated),
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
            regulated=_regulated(pricing, regulated),
            provenance=prov,
        )
    except ValueError as err:
        assert "must be COMMODITY" in str(err)
    else:
        raise AssertionError("Wrong commercial component kind must be rejected")


def test_all_in_assembly_uses_actual_validity_intersection_and_rejects_no_overlap() -> None:
    pricing, sources, provenance, regulated, assembly = load_modules()
    prov = _provenance(sources, provenance)

    result = assembly.assemble_all_in_tariff(
        supplier="ČEZ",
        product_name="Basic",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        commercial_valid_from=date(2026, 3, 1),
        commercial_valid_to=date(2026, 10, 31),
        commodity=_commodity(pricing),
        supplier_fixed=_supplier_fixed(pricing),
        regulated=_regulated(pricing, regulated),
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
            regulated=_regulated(pricing, regulated),
            provenance=prov,
        )
    except ValueError as err:
        assert "do not overlap" in str(err)
    else:
        raise AssertionError("Non-overlapping all-in sources must be rejected")
