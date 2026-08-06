from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from typing import Iterable

MONEY = Decimal("0.01")
ENERGY = Decimal("0.001")


class DataQuality(StrEnum):
    MEASURED = "measured"
    CALCULATED = "calculated"
    ESTIMATED = "estimated"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True)
class DailyEnergyRecord:
    day: date
    high_rate_kwh: Decimal
    low_rate_kwh: Decimal
    high_rate_price_czk_kwh: Decimal | None
    low_rate_price_czk_kwh: Decimal | None
    fixed_cost_czk: Decimal = Decimal("0")
    quality: DataQuality = DataQuality.MEASURED

    def __post_init__(self) -> None:
        if self.high_rate_kwh < 0 or self.low_rate_kwh < 0:
            raise ValueError("daily consumption cannot be negative")
        if self.fixed_cost_czk < 0:
            raise ValueError("fixed cost cannot be negative")

    @property
    def total_kwh(self) -> Decimal:
        return (self.high_rate_kwh + self.low_rate_kwh).quantize(ENERGY)

    @property
    def variable_cost_czk(self) -> Decimal | None:
        if self.high_rate_price_czk_kwh is None or self.low_rate_price_czk_kwh is None:
            return None
        value = (
            self.high_rate_kwh * self.high_rate_price_czk_kwh
            + self.low_rate_kwh * self.low_rate_price_czk_kwh
        )
        return value.quantize(MONEY, rounding=ROUND_HALF_UP)

    @property
    def total_cost_czk(self) -> Decimal | None:
        variable = self.variable_cost_czk
        if variable is None:
            return None
        return (variable + self.fixed_cost_czk).quantize(MONEY, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class MonthlyEnergyRecord:
    year: int
    month: int
    high_rate_kwh: Decimal
    low_rate_kwh: Decimal
    variable_cost_czk: Decimal | None
    fixed_cost_czk: Decimal
    total_cost_czk: Decimal | None
    quality: DataQuality
    complete_days: int
    total_days: int

    @property
    def total_kwh(self) -> Decimal:
        return (self.high_rate_kwh + self.low_rate_kwh).quantize(ENERGY)


def aggregate_month(records: Iterable[DailyEnergyRecord], *, year: int, month: int) -> MonthlyEnergyRecord:
    selected = sorted((record for record in records if record.day.year == year and record.day.month == month), key=lambda item: item.day)
    if not selected:
        raise ValueError("month has no daily records")

    high = sum((item.high_rate_kwh for item in selected), Decimal("0"))
    low = sum((item.low_rate_kwh for item in selected), Decimal("0"))
    fixed = sum((item.fixed_cost_czk for item in selected), Decimal("0"))
    variable_values = [item.variable_cost_czk for item in selected]
    complete = [value for value in variable_values if value is not None]
    variable = sum(complete, Decimal("0")).quantize(MONEY) if len(complete) == len(selected) else None
    total = (variable + fixed).quantize(MONEY) if variable is not None else None

    quality_order = {
        DataQuality.MEASURED: 0,
        DataQuality.CALCULATED: 1,
        DataQuality.ESTIMATED: 2,
        DataQuality.INCOMPLETE: 3,
    }
    quality = max((item.quality for item in selected), key=quality_order.__getitem__)
    if variable is None:
        quality = DataQuality.INCOMPLETE

    return MonthlyEnergyRecord(
        year=year,
        month=month,
        high_rate_kwh=high.quantize(ENERGY),
        low_rate_kwh=low.quantize(ENERGY),
        variable_cost_czk=variable,
        fixed_cost_czk=fixed.quantize(MONEY),
        total_cost_czk=total,
        quality=quality,
        complete_days=len(complete),
        total_days=len(selected),
    )


def aggregate_year(records: Iterable[DailyEnergyRecord], *, year: int) -> tuple[MonthlyEnergyRecord, ...]:
    materialized = tuple(records)
    months = sorted({record.day.month for record in materialized if record.day.year == year})
    return tuple(aggregate_month(materialized, year=year, month=month) for month in months)
