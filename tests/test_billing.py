from datetime import date
from decimal import Decimal

import pytest

from custom_components.frakon_energy.billing import (
    AdvancePeriod,
    BillingCalculator,
    BillingCycle,
    MeterBaseline,
)


def test_billing_snapshot_with_5000_czk_monthly_advance():
    cycle = BillingCycle(
        start_date=date(2026, 1, 1),
        expected_settlement_date=date(2026, 12, 31),
        baseline=MeterBaseline(
            reading_date=date(2025, 12, 31),
            high_rate_kwh=Decimal("1000"),
            low_rate_kwh=Decimal("2000"),
        ),
    )
    snapshot = BillingCalculator.calculate(
        cycle=cycle,
        as_of=date(2026, 5, 15),
        advances=(AdvancePeriod(date(2026, 1, 1), Decimal("5000")),),
        accrued_cost_czk=Decimal("22400"),
        projected_total_cost_czk=Decimal("63800"),
    )

    assert snapshot.paid_advances_czk == Decimal("25000.00")
    assert snapshot.current_balance_czk == Decimal("2600.00")
    assert snapshot.projected_total_advances_czk == Decimal("60000.00")
    assert snapshot.projected_settlement_balance_czk == Decimal("-3800.00")
    assert snapshot.recommended_monthly_advance_czk == Decimal("5542.86")


def test_newer_advance_period_overrides_older_one():
    cycle = BillingCycle(
        start_date=date(2026, 1, 1),
        expected_settlement_date=date(2026, 12, 31),
        baseline=MeterBaseline(date(2025, 12, 31), Decimal("0"), Decimal("0")),
    )
    snapshot = BillingCalculator.calculate(
        cycle=cycle,
        as_of=date(2026, 4, 30),
        advances=(
            AdvancePeriod(date(2026, 1, 1), Decimal("5000"), date(2026, 2, 28)),
            AdvancePeriod(date(2026, 3, 1), Decimal("6000")),
        ),
        accrued_cost_czk=Decimal("0"),
        projected_total_cost_czk=Decimal("0"),
    )

    assert snapshot.paid_advances_czk == Decimal("22000.00")
    assert snapshot.projected_total_advances_czk == Decimal("70000.00")


def test_invalid_cycle_is_rejected():
    with pytest.raises(ValueError):
        BillingCycle(
            start_date=date(2026, 12, 31),
            expected_settlement_date=date(2026, 1, 1),
            baseline=MeterBaseline(date(2025, 12, 31), Decimal("0"), Decimal("0")),
        )
