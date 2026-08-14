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
        "custom_components.frakon_energy.providers",
        "custom_components.frakon_energy.pricing",
        "custom_components.frakon_energy.providers.cez_tariff_parser",
        "custom_components.frakon_energy.providers.cez_tariff_components",
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
    parser = load(
        "custom_components.frakon_energy.providers.cez_tariff_parser",
        "custom_components/frakon_energy/providers/cez_tariff_parser.py",
    )
    components = load(
        "custom_components.frakon_energy.providers.cez_tariff_components",
        "custom_components/frakon_energy/providers/cez_tariff_components.py",
    )
    return pricing, parser, components


def test_dual_rate_parsed_price_maps_only_to_supplier_components() -> None:
    pricing, parser, components = load_modules()
    parsed = parser.ParsedCezCommercialPrice(
        product_name="Basic",
        valid_from=date(2026, 1, 1),
        distribution_tariff="D25d",
        high_rate_czk_per_kwh=Decimal("3.960"),
        low_rate_czk_per_kwh=Decimal("3.700"),
        supplier_standing_czk_month=Decimal("130.68"),
    )

    result = components.components_from_parsed_cez_commercial_price(parsed)

    assert result.commodity.kind == pricing.PriceComponentKind.COMMODITY
    assert result.commodity.high_rate_czk_per_kwh == Decimal("3.960")
    assert result.commodity.low_rate_czk_per_kwh == Decimal("3.700")
    assert result.commodity.includes_vat is True
    assert result.supplier_fixed.kind == pricing.PriceComponentKind.SUPPLIER_FIXED
    assert result.supplier_fixed.monthly_czk == Decimal("130.68")
    assert result.supplier_fixed.includes_vat is True
    assert result.commodity.kind != pricing.PriceComponentKind.DISTRIBUTION


def test_single_rate_price_is_not_silently_forced_into_dual_rate_model() -> None:
    _, parser, components = load_modules()
    parsed = parser.ParsedCezCommercialPrice(
        product_name="Basic",
        valid_from=date(2026, 1, 1),
        distribution_tariff="D01d",
        high_rate_czk_per_kwh=Decimal("3.860"),
        low_rate_czk_per_kwh=None,
        supplier_standing_czk_month=Decimal("147.62"),
    )

    try:
        components.components_from_parsed_cez_commercial_price(parsed)
    except ValueError as err:
        assert "single-rate" in str(err)
    else:
        raise AssertionError("Single-rate tariff must not be coerced into dual-rate pricing")


def test_mapping_rejects_untrusted_object() -> None:
    _, _, components = load_modules()
    try:
        components.components_from_parsed_cez_commercial_price(object())
    except ValueError as err:
        assert "ParsedCezCommercialPrice" in str(err)
    else:
        raise AssertionError("Unexpected parsed object type must be rejected")
