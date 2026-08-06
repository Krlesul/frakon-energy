from datetime import date
from decimal import Decimal

import pytest

from custom_components.frakon_energy.history_aggregation import (
    DataQuality,
    DailyEnergyRecord,
    aggregate_month,
    aggregate_year,
)


def test_daily_record_calculates_vt_nt_variable_and_total_cost() -> None:
    record = DailyEnergyRecord(
        day=date(2026, 8, 1),
        high_rate_kwh=Decimal("8.2"),
        low_rate_kwh=Decimal("16.6"),
        high_rate_price_czk_kwh=Decimal("7.52"),
        low_rate_price_czk_kwh=Decimal("4.67"),
        fixed_cost_czk=Decimal("13.80"),
    )

    assert record.total_kwh == Decimal("24.800")
    assert record.variable_cost_czk == Decimal("139.16")
    assert record.total_cost_czk == Decimal("152.96")


def test_month_aggregates_consumption_cost_and_quality() -> None:
    records = (
        DailyEnergyRecord(date(2026, 8, 1), Decimal("8"), Decimal("12"), Decimal("7"), Decimal("4"), Decimal("10")),
        DailyEnergyRecord(date(2026, 8, 2), Decimal("5"), Decimal("15"), Decimal("7"), Decimal("4"), Decimal("10"), DataQuality.CALCULATED),
    )

    month = aggregate_month(records, year=2026, month=8)

    assert month.high_rate_kwh == Decimal("13.000")
    assert month.low_rate_kwh == Decimal("27.000")
    assert month.variable_cost_czk == Decimal("199.00")
    assert month.fixed_cost_czk == Decimal("20.00")
    assert month.total_cost_czk == Decimal("219.00")
    assert month.quality == DataQuality.CALCULATED
    assert month.complete_days == 2


def test_missing_price_marks_month_incomplete_without_inventing_cost() -> None:
    records = (
        DailyEnergyRecord(date(2026, 8, 1), Decimal("2"), Decimal("3"), Decimal("7"), Decimal("4")),
        DailyEnergyRecord(date(2026, 8, 2), Decimal("2"), Decimal("3"), None, Decimal("4")),
    )

    month = aggregate_month(records, year=2026, month=8)

    assert month.variable_cost_czk is None
    assert month.total_cost_czk is None
    assert month.quality == DataQuality.INCOMPLETE
    assert month.complete_days == 1
    assert month.total_days == 2


def test_year_returns_only_months_with_records() -> None:
    records = (
        DailyEnergyRecord(date(2026, 1, 1), Decimal("1"), Decimal("2"), Decimal("7"), Decimal("4")),
        DailyEnergyRecord(date(2026, 3, 1), Decimal("3"), Decimal("4"), Decimal("7"), Decimal("4")),
    )

    year = aggregate_year(records, year=2026)

    assert [item.month for item in year] == [1, 3]


def test_negative_consumption_is_rejected() -> None:
    with pytest.raises(ValueError):
        DailyEnergyRecord(date(2026, 8, 1), Decimal("-1"), Decimal("0"), Decimal("7"), Decimal("4"))
