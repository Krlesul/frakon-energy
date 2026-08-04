from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from .billing import (
    AdvancePeriod,
    BillingCalculator,
    BillingCycle,
    BillingSnapshot,
)
from .tariff import ConsumptionSlice, CostBreakdown, TariffCalculator, TariffPeriod


@dataclass(frozen=True, slots=True)
class MeterReading:
    """Cumulative VT/NT meter reading at a specific date."""

    reading_date: date
    high_rate_kwh: Decimal
    low_rate_kwh: Decimal


@dataclass(frozen=True, slots=True)
class SettlementSnapshot:
    """Combined consumption, cost and advance state for one billing cycle."""

    consumption_high_rate_kwh: Decimal
    consumption_low_rate_kwh: Decimal
    cost: CostBreakdown
    billing: BillingSnapshot
    actual_data_through: date
    forecast_used: bool


class SettlementCalculator:
    """Combine VisionQ consumption, tariffs and advances.

    Historical daily slices are required when a billing period spans multiple
    tariff periods. This prevents silently applying today's tariff backwards.
    """

    @classmethod
    def calculate(
        cls,
        *,
        cycle: BillingCycle,
        current_reading: MeterReading,
        daily_consumption: Iterable[ConsumptionSlice],
        tariffs: Iterable[TariffPeriod],
        advances: Iterable[AdvancePeriod],
        projected_total_cost_czk: Decimal,
        as_of: date | None = None,
        forecast_used: bool = False,
    ) -> SettlementSnapshot:
        as_of = as_of or current_reading.reading_date
        if current_reading.reading_date < cycle.start_date:
            raise ValueError("Current reading predates billing cycle")
        if as_of != current_reading.reading_date:
            raise ValueError("as_of must match current reading date")

        high_delta = current_reading.high_rate_kwh - cycle.baseline.high_rate_kwh
        low_delta = current_reading.low_rate_kwh - cycle.baseline.low_rate_kwh
        if high_delta < 0 or low_delta < 0:
            raise ValueError("Current meter reading is below billing baseline")

        slices = tuple(daily_consumption)
        slice_high = sum((item.high_rate_kwh for item in slices), Decimal("0"))
        slice_low = sum((item.low_rate_kwh for item in slices), Decimal("0"))
        if slice_high != high_delta or slice_low != low_delta:
            raise ValueError("Daily consumption does not match meter delta")

        cost = TariffCalculator.calculate(consumption=slices, tariffs=tariffs)
        billing = BillingCalculator.calculate(
            cycle=cycle,
            as_of=as_of,
            advances=advances,
            accrued_cost_czk=cost.total_czk,
            projected_total_cost_czk=projected_total_cost_czk,
        )
        return SettlementSnapshot(
            consumption_high_rate_kwh=cost.high_rate_kwh,
            consumption_low_rate_kwh=cost.low_rate_kwh,
            cost=cost,
            billing=billing,
            actual_data_through=current_reading.reading_date,
            forecast_used=forecast_used,
        )
