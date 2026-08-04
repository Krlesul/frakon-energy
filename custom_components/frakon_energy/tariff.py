from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

MONEY_QUANT = Decimal("0.01")
ENERGY_QUANT = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class TariffPeriod:
    """One electricity tariff valid for a bounded date range.

    All monetary values are stored including VAT. Energy prices use CZK/MWh;
    fixed charges use CZK/month.
    """

    valid_from: date
    valid_to: date | None
    supplier: str
    product: str
    distribution_rate: str
    high_rate_supply_czk_mwh: Decimal
    low_rate_supply_czk_mwh: Decimal
    high_rate_distribution_czk_mwh: Decimal
    low_rate_distribution_czk_mwh: Decimal
    electricity_tax_czk_mwh: Decimal
    system_services_czk_mwh: Decimal
    poze_consumption_czk_mwh: Decimal
    supplier_monthly_fee_czk: Decimal
    breaker_monthly_fee_czk: Decimal
    infrastructure_monthly_fee_czk: Decimal

    def applies_on(self, day: date) -> bool:
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)

    @property
    def high_rate_total_czk_mwh(self) -> Decimal:
        return (
            self.high_rate_supply_czk_mwh
            + self.high_rate_distribution_czk_mwh
            + self.electricity_tax_czk_mwh
            + self.system_services_czk_mwh
            + self.poze_consumption_czk_mwh
        )

    @property
    def low_rate_total_czk_mwh(self) -> Decimal:
        return (
            self.low_rate_supply_czk_mwh
            + self.low_rate_distribution_czk_mwh
            + self.electricity_tax_czk_mwh
            + self.system_services_czk_mwh
            + self.poze_consumption_czk_mwh
        )

    @property
    def monthly_fixed_czk(self) -> Decimal:
        return (
            self.supplier_monthly_fee_czk
            + self.breaker_monthly_fee_czk
            + self.infrastructure_monthly_fee_czk
        )


@dataclass(frozen=True, slots=True)
class ConsumptionSlice:
    """Measured consumption assigned to a single calendar day."""

    day: date
    high_rate_kwh: Decimal
    low_rate_kwh: Decimal


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    variable_high_rate_czk: Decimal
    variable_low_rate_czk: Decimal
    fixed_czk: Decimal
    total_czk: Decimal
    high_rate_kwh: Decimal
    low_rate_kwh: Decimal


class TariffCalculator:
    """Calculate costs across tariff changes inside one billing cycle."""

    @classmethod
    def calculate(
        cls,
        *,
        consumption: Iterable[ConsumptionSlice],
        tariffs: Iterable[TariffPeriod],
        include_fixed_charges: bool = True,
    ) -> CostBreakdown:
        tariff_periods = tuple(sorted(tariffs, key=lambda item: item.valid_from))
        if not tariff_periods:
            raise ValueError("At least one tariff period is required")

        high_cost = Decimal("0")
        low_cost = Decimal("0")
        total_high_kwh = Decimal("0")
        total_low_kwh = Decimal("0")
        used_months: set[tuple[int, int, date]] = set()

        for item in consumption:
            tariff = cls._tariff_for_day(item.day, tariff_periods)
            if tariff is None:
                raise ValueError(f"No tariff configured for {item.day.isoformat()}")

            total_high_kwh += item.high_rate_kwh
            total_low_kwh += item.low_rate_kwh
            high_cost += item.high_rate_kwh * tariff.high_rate_total_czk_mwh / Decimal("1000")
            low_cost += item.low_rate_kwh * tariff.low_rate_total_czk_mwh / Decimal("1000")
            used_months.add((item.day.year, item.day.month, tariff.valid_from))

        fixed = Decimal("0")
        if include_fixed_charges:
            for year, month, tariff_valid_from in used_months:
                tariff = next(t for t in tariff_periods if t.valid_from == tariff_valid_from)
                fixed += tariff.monthly_fixed_czk

        total = high_cost + low_cost + fixed
        return CostBreakdown(
            variable_high_rate_czk=_money(high_cost),
            variable_low_rate_czk=_money(low_cost),
            fixed_czk=_money(fixed),
            total_czk=_money(total),
            high_rate_kwh=total_high_kwh.quantize(ENERGY_QUANT),
            low_rate_kwh=total_low_kwh.quantize(ENERGY_QUANT),
        )

    @staticmethod
    def _tariff_for_day(day: date, tariffs: tuple[TariffPeriod, ...]) -> TariffPeriod | None:
        matches = [tariff for tariff in tariffs if tariff.applies_on(day)]
        if not matches:
            return None
        return max(matches, key=lambda tariff: tariff.valid_from)


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
