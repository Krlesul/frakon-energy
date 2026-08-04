from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

MONEY_QUANT = Decimal("0.01")
DEFAULT_SETTLEMENT_MONTH = 1
DEFAULT_SETTLEMENT_DAY = 31


def next_default_settlement_date(reference_date: date) -> date:
    """Return the next default annual settlement date.

    FRAKON Energy defaults to 31 January every year. If the reference date is
    already past 31 January, the next settlement date is 31 January of the
    following year. The user may override this value in the UI.
    """

    candidate = date(
        reference_date.year,
        DEFAULT_SETTLEMENT_MONTH,
        DEFAULT_SETTLEMENT_DAY,
    )
    if candidate < reference_date:
        candidate = date(
            reference_date.year + 1,
            DEFAULT_SETTLEMENT_MONTH,
            DEFAULT_SETTLEMENT_DAY,
        )
    return candidate


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

    @classmethod
    def with_default_settlement_date(
        cls,
        *,
        start_date: date,
        baseline: MeterBaseline,
    ) -> BillingCycle:
        """Create a cycle using the default 31 January settlement date."""

        return cls(
            start_date=start_date,
            expected_settlement_date=next_default_settlement_date(start_date),
            baseline=baseline,
        )


@dataclass(frozen=True, slots=True)
class BillingSnapshot:
    as_of: date
    paid_advances_czk: Decimal
    accrued_cost_czk: Decimal
    current_balance_czk: Decimal
    projected_total_cost_czk: Decimal
    projected_total_advances_czk: Decimal
    projected_settlement_balance_czk: Decimal
    recommended_monthly_advance_czk: Decimal


class BillingCalculator:
    @classmethod
    def calculate(
        cls,
        *,
        cycle: BillingCycle,
        as_of: date,
        advances: Iterable[AdvancePeriod],
        accrued_cost_czk: Decimal,
        projected_total_cost_czk: Decimal,
    ) -> BillingSnapshot:
        if not cycle.start_date <= as_of <= cycle.expected_settlement_date:
            raise ValueError("as_of must fall inside the billing cycle")

        periods = tuple(advances)
        paid = cls._sum_advances(cycle.start_date, as_of, periods)
        projected_paid = cls._sum_advances(
            cycle.start_date, cycle.expected_settlement_date, periods
        )
        current_balance = paid - accrued_cost_czk
        projected_balance = projected_paid - projected_total_cost_czk

        remaining_months = cls._count_months(
            cls._next_month(as_of), cycle.expected_settlement_date
        )
        remaining_cost = max(Decimal("0"), projected_total_cost_czk - paid)
        recommended = (
            remaining_cost / remaining_months
            if remaining_months > 0
            else max(Decimal("0"), -projected_balance)
        )

        return BillingSnapshot(
            as_of=as_of,
            paid_advances_czk=_money(paid),
            accrued_cost_czk=_money(accrued_cost_czk),
            current_balance_czk=_money(current_balance),
            projected_total_cost_czk=_money(projected_total_cost_czk),
            projected_total_advances_czk=_money(projected_paid),
            projected_settlement_balance_czk=_money(projected_balance),
            recommended_monthly_advance_czk=_money(recommended),
        )

    @classmethod
    def _sum_advances(
        cls, start: date, end: date, periods: tuple[AdvancePeriod, ...]
    ) -> Decimal:
        total = Decimal("0")
        for month in cls._month_starts(start, end):
            applicable = [period for period in periods if period.applies_on(month)]
            if applicable:
                total += max(applicable, key=lambda item: item.valid_from).monthly_amount_czk
        return total

    @classmethod
    def _month_starts(cls, start: date, end: date):
        current = date(start.year, start.month, 1)
        last = date(end.year, end.month, 1)
        while current <= last:
            yield current
            current = cls._next_month(current)

    @staticmethod
    def _next_month(day: date) -> date:
        return date(day.year + (day.month == 12), 1 if day.month == 12 else day.month + 1, 1)

    @staticmethod
    def _count_months(start: date, end: date) -> int:
        if start > end:
            return 0
        return (end.year - start.year) * 12 + end.month - start.month + 1


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
