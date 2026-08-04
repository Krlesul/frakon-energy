from datetime import date
from decimal import Decimal

import pytest

from custom_components.frakon_energy.billing import AdvancePeriod, BillingCycle, MeterBaseline
from custom_components.frakon_energy.settlement import MeterReading, SettlementCalculator
from custom_components.frakon_energy.tariff import ConsumptionSlice
from tests.test_tariff import cez_d25d_2026


def test_settlement_combines_visionq_delta_cost_and_advances():
    cycle = BillingCycle(
        start_date=date(2026, 2, 1),
        expected_settlement_date=date(2027, 1, 31),
        baseline=MeterBaseline(date(2026, 1, 31), Decimal("300"), Decimal("300")),
    )
    result = SettlementCalculator.calculate(
        cycle=cycle,
        current_reading=MeterReading(date(2026, 2, 2), Decimal("315"), Decimal("335")),
        daily_consumption=(
            ConsumptionSlice(date(2026, 2, 1), Decimal("10"), Decimal("20")),
            ConsumptionSlice(date(2026, 2, 2), Decimal("5"), Decimal("15")),
        ),
        tariffs=(cez_d25d_2026(),),
        advances=(AdvancePeriod(date(2026, 2, 1), Decimal("5000")),),
        projected_total_cost_czk=Decimal("63800"),
    )

    assert result.consumption_high_rate_kwh == Decimal("15.000")
    assert result.consumption_low_rate_kwh == Decimal("35.000")
    assert result.cost.total_czk == Decimal("763.78")
    assert result.billing.paid_advances_czk == Decimal("5000.00")
    assert result.billing.current_balance_czk == Decimal("4236.22")
    assert result.actual_data_through == date(2026, 2, 2)
    assert result.forecast_used is False


def test_settlement_rejects_daily_history_that_does_not_match_meter():
    cycle = BillingCycle(
        start_date=date(2026, 2, 1),
        expected_settlement_date=date(2027, 1, 31),
        baseline=MeterBaseline(date(2026, 1, 31), Decimal("300"), Decimal("300")),
    )
    with pytest.raises(ValueError, match="does not match meter delta"):
        SettlementCalculator.calculate(
            cycle=cycle,
            current_reading=MeterReading(date(2026, 2, 1), Decimal("310"), Decimal("320")),
            daily_consumption=(ConsumptionSlice(date(2026, 2, 1), Decimal("9"), Decimal("20")),),
            tariffs=(cez_d25d_2026(),),
            advances=(AdvancePeriod(date(2026, 2, 1), Decimal("5000")),),
            projected_total_cost_czk=Decimal("60000"),
        )


def test_settlement_rejects_meter_reset_or_wrong_baseline():
    cycle = BillingCycle(
        start_date=date(2026, 2, 1),
        expected_settlement_date=date(2027, 1, 31),
        baseline=MeterBaseline(date(2026, 1, 31), Decimal("500"), Decimal("500")),
    )
    with pytest.raises(ValueError, match="below billing baseline"):
        SettlementCalculator.calculate(
            cycle=cycle,
            current_reading=MeterReading(date(2026, 2, 1), Decimal("499"), Decimal("510")),
            daily_consumption=(),
            tariffs=(cez_d25d_2026(),),
            advances=(AdvancePeriod(date(2026, 2, 1), Decimal("5000")),),
            projected_total_cost_czk=Decimal("60000"),
        )
