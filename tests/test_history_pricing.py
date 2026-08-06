from datetime import date
from decimal import Decimal

from custom_components.frakon_energy.history_aggregation import DataQuality
from custom_components.frakon_energy.history_pricing import (
    DailyConsumption,
    allocate_fixed_cost_for_day,
    price_daily_consumption,
)
from custom_components.frakon_energy.pricing import (
    AllInTariffPrice,
    FixedPriceComponent,
    PriceComponentKind,
    PriceSource,
    VariablePriceComponent,
)


def tariff(
    *,
    valid_from: date,
    valid_to: date | None,
    vt: str,
    nt: str,
    fixed: str,
    product: str,
) -> AllInTariffPrice:
    return AllInTariffPrice(
        source=PriceSource(
            supplier="Test Energy",
            product=product,
            valid_from=valid_from,
            valid_to=valid_to,
            confirmed=True,
        ),
        variable_components=(
            VariablePriceComponent(
                PriceComponentKind.COMMODITY,
                "All-in variable price",
                Decimal(vt),
                Decimal(nt),
            ),
        ),
        fixed_components=(
            FixedPriceComponent(
                PriceComponentKind.SUPPLIER_FIXED,
                "Fixed monthly payment",
                Decimal(fixed),
            ),
        ),
    )


def test_daily_history_uses_tariff_valid_for_exact_day() -> None:
    old = tariff(
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 8, 14),
        vt="7.00",
        nt="4.00",
        fixed="310",
        product="Old",
    )
    new = tariff(
        valid_from=date(2026, 8, 15),
        valid_to=None,
        vt="8.00",
        nt="5.00",
        fixed="620",
        product="New",
    )
    source = (
        DailyConsumption(date(2026, 8, 14), Decimal("2"), Decimal("3")),
        DailyConsumption(date(2026, 8, 15), Decimal("2"), Decimal("3")),
    )

    records = price_daily_consumption(source, (old, new))

    assert records[0].high_rate_price_czk_kwh == Decimal("7.00")
    assert records[0].low_rate_price_czk_kwh == Decimal("4.00")
    assert records[0].variable_cost_czk == Decimal("26.00")
    assert records[0].fixed_cost_czk == Decimal("10")

    assert records[1].high_rate_price_czk_kwh == Decimal("8.00")
    assert records[1].low_rate_price_czk_kwh == Decimal("5.00")
    assert records[1].variable_cost_czk == Decimal("31.00")
    assert records[1].fixed_cost_czk == Decimal("20")


def test_missing_tariff_produces_incomplete_record_without_fallback() -> None:
    source = (DailyConsumption(date(2026, 8, 1), Decimal("1"), Decimal("2")),)

    records = price_daily_consumption(source, ())

    assert records[0].quality == DataQuality.INCOMPLETE
    assert records[0].variable_cost_czk is None
    assert records[0].fixed_cost_czk == Decimal("0")


def test_fixed_monthly_payment_is_allocated_by_calendar_days() -> None:
    price = tariff(
        valid_from=date(2026, 1, 1),
        valid_to=None,
        vt="7",
        nt="4",
        fixed="280",
        product="Fixed",
    )

    assert allocate_fixed_cost_for_day(price, date(2026, 2, 10)) == Decimal("10")


def test_source_quality_is_preserved_when_price_is_available() -> None:
    price = tariff(
        valid_from=date(2026, 1, 1),
        valid_to=None,
        vt="7",
        nt="4",
        fixed="0",
        product="Estimated",
    )
    source = (
        DailyConsumption(
            date(2026, 8, 1),
            Decimal("1"),
            Decimal("2"),
            quality=DataQuality.ESTIMATED,
        ),
    )

    records = price_daily_consumption(source, (price,))

    assert records[0].quality == DataQuality.ESTIMATED
