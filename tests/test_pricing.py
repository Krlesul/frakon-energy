from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys


def load_pricing():
    path = Path("custom_components/frakon_energy/pricing.py")
    spec = importlib.util.spec_from_file_location("frakon_energy_pricing", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_all_in_price_sums_variable_and_fixed_components() -> None:
    pricing = load_pricing()
    tariff = pricing.AllInTariffPrice(
        source=pricing.PriceSource(
            supplier="ČEZ",
            product="Elektřina na 3 roky",
            valid_from=date(2026, 1, 1),
            confirmed=True,
        ),
        variable_components=(
            pricing.VariablePriceComponent(pricing.PriceComponentKind.COMMODITY, "Silová elektřina", Decimal("3.20"), Decimal("2.80")),
            pricing.VariablePriceComponent(pricing.PriceComponentKind.DISTRIBUTION, "Distribuce", Decimal("2.10"), Decimal("0.75")),
            pricing.VariablePriceComponent(pricing.PriceComponentKind.POZE, "POZE", Decimal("0.60"), Decimal("0.60")),
            pricing.VariablePriceComponent(pricing.PriceComponentKind.SYSTEM_SERVICES, "Systémové služby", Decimal("0.25"), Decimal("0.25")),
            pricing.VariablePriceComponent(pricing.PriceComponentKind.ELECTRICITY_TAX, "Daň", Decimal("0.034"), Decimal("0.034")),
        ),
        fixed_components=(
            pricing.FixedPriceComponent(pricing.PriceComponentKind.SUPPLIER_FIXED, "Stálý plat dodavateli", Decimal("129")),
            pricing.FixedPriceComponent(pricing.PriceComponentKind.BREAKER_FIXED, "Jistič 3x25 A", Decimal("286")),
        ),
    )

    assert tariff.high_rate_czk_per_kwh == Decimal("6.184")
    assert tariff.low_rate_czk_per_kwh == Decimal("4.434")
    assert tariff.fixed_monthly_czk == Decimal("415")


def test_latest_overlapping_validity_period_wins() -> None:
    pricing = load_pricing()
    old = pricing.AllInTariffPrice(
        source=pricing.PriceSource("ČEZ", "Produkt", date(2026, 1, 1), date(2026, 6, 30)),
        variable_components=(),
        fixed_components=(),
    )
    new = pricing.AllInTariffPrice(
        source=pricing.PriceSource("ČEZ", "Produkt", date(2026, 6, 1)),
        variable_components=(),
        fixed_components=(),
    )
    assert pricing.select_price_for_day((old, new), date(2026, 6, 15)) is new


def test_negative_price_component_is_rejected() -> None:
    pricing = load_pricing()
    try:
        pricing.VariablePriceComponent(
            pricing.PriceComponentKind.DISTRIBUTION,
            "Distribuce",
            Decimal("-1"),
            Decimal("0"),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Negative variable price must be rejected")
