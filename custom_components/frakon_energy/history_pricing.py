from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from .history_aggregation import DataQuality, DailyEnergyRecord
from .pricing import AllInTariffPrice, select_price_for_day


@dataclass(frozen=True, slots=True)
class DailyConsumption:
    """Measured or derived VT/NT consumption for one calendar day."""

    day: date
    high_rate_kwh: Decimal
    low_rate_kwh: Decimal
    quality: DataQuality = DataQuality.MEASURED

    def __post_init__(self) -> None:
        if self.high_rate_kwh < 0 or self.low_rate_kwh < 0:
            raise ValueError("daily consumption cannot be negative")


def allocate_fixed_cost_for_day(price: AllInTariffPrice, day: date) -> Decimal:
    """Allocate the tariff's monthly fixed charges evenly across the calendar month."""

    days_in_month = monthrange(day.year, day.month)[1]
    return price.fixed_monthly_czk / Decimal(days_in_month)


def price_daily_consumption(
    consumption: Iterable[DailyConsumption],
    prices: Iterable[AllInTariffPrice],
) -> tuple[DailyEnergyRecord, ...]:
    """Apply the all-in tariff valid on each day to a daily consumption series.

    Historical price periods are preserved: a price change during a month affects only
    records on and after its validity date. Missing prices produce an incomplete record
    instead of silently applying a neighbouring tariff.
    """

    price_catalog = tuple(prices)
    records: list[DailyEnergyRecord] = []

    for item in sorted(consumption, key=lambda value: value.day):
        try:
            tariff = select_price_for_day(price_catalog, item.day)
        except LookupError:
            records.append(
                DailyEnergyRecord(
                    day=item.day,
                    high_rate_kwh=item.high_rate_kwh,
                    low_rate_kwh=item.low_rate_kwh,
                    high_rate_price_czk_kwh=None,
                    low_rate_price_czk_kwh=None,
                    fixed_cost_czk=Decimal("0"),
                    quality=DataQuality.INCOMPLETE,
                )
            )
            continue

        records.append(
            DailyEnergyRecord(
                day=item.day,
                high_rate_kwh=item.high_rate_kwh,
                low_rate_kwh=item.low_rate_kwh,
                high_rate_price_czk_kwh=tariff.high_rate_czk_per_kwh,
                low_rate_price_czk_kwh=tariff.low_rate_czk_per_kwh,
                fixed_cost_czk=allocate_fixed_cost_for_day(tariff, item.day),
                quality=item.quality,
            )
        )

    return tuple(records)
