from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class MeterBaseline:
    reading_date: date
    high_rate_kwh: Decimal
    low_rate_kwh: Decimal

    @property
    def total_kwh(self) -> Decimal:
        return self.high_rate_kwh + self.low_rate_kwh


@dataclass(frozen=True, slots=True)
class AdvancePeriod:
    valid_from: date
    monthly_amount_czk: Decimal
    valid_to: date | None = None

    def applies_on(self, day: date) -> bool:
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)


@dataclass(frozen=True, slots=True)
class BillingCycle:
    start_date: date
    expected_settlement_date: date
    baseline: MeterBaseline

    def __post_init__(self) -> None:
        if self.expected_settlement_date < self.start_date:
            raise ValueError("Settlement date must not precede cycle start")
        if self.baseline.reading_date > self.start_date:
            raise ValueError("Baseline date must not be after cycle start")
