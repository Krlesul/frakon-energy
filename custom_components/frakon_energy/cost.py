from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from types import MappingProxyType
from typing import Any, Mapping

MONEY_QUANT = Decimal("0.01")
ENERGY_QUANT = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class TariffPrices:
    high_rate_czk_per_kwh: Decimal
    low_rate_czk_per_kwh: Decimal
    fixed_monthly_czk: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class CatalogTariffPrices(TariffPrices):
    """Current price view carrying a read-only immutable-catalog snapshot.

    The numeric fields remain the exact price for the selected day and are used
    by UI attributes. Cost projection detects this subtype and prices the whole
    billing cycle with the historical confirmed all-in schedule instead of
    repricing every day with the current version.
    """

    catalog_options: Mapping[str, Any] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.catalog_options, Mapping):
            raise ValueError("catalog_options must be a mapping")
        object.__setattr__(
            self,
            "catalog_options",
            MappingProxyType(dict(self.catalog_options)),
        )


@dataclass(frozen=True, slots=True)
class CostProjection:
    high_rate_consumption_kwh: Decimal
    low_rate_consumption_kwh: Decimal
    accrued_energy_cost_czk: Decimal
    accrued_fixed_cost_czk: Decimal
    accrued_total_cost_czk: Decimal
    projected_total_cost_czk: Decimal


def calculate_cost_projection(
    *,
    cycle_start: date,
    settlement_date: date,
    as_of: date,
    baseline_high_rate_kwh: Decimal,
    baseline_low_rate_kwh: Decimal,
    current_high_rate_kwh: Decimal,
    current_low_rate_kwh: Decimal,
    prices: TariffPrices,
) -> CostProjection:
    """Calculate accrued cost and a transparent linear settlement projection.

    Legacy ``TariffPrices`` keeps the original single-price calculation.
    ``CatalogTariffPrices`` carries the immutable customer tariff catalog and is
    routed through the historical confirmed all-in schedule so a later price-list
    version cannot reprice earlier days in the same billing cycle.
    """
    if isinstance(prices, CatalogTariffPrices):
        from .billing_all_in_history import calculate_confirmed_all_in_cost_projection

        return calculate_confirmed_all_in_cost_projection(
            prices.catalog_options,
            cycle_start=cycle_start,
            settlement_date=settlement_date,
            as_of=as_of,
            baseline_high_rate_kwh=baseline_high_rate_kwh,
            baseline_low_rate_kwh=baseline_low_rate_kwh,
            current_high_rate_kwh=current_high_rate_kwh,
            current_low_rate_kwh=current_low_rate_kwh,
        ).cost

    if settlement_date < cycle_start:
        raise ValueError("Settlement date must not precede cycle start")
    if not cycle_start <= as_of <= settlement_date:
        raise ValueError("as_of must fall inside the billing cycle")

    high = max(Decimal("0"), current_high_rate_kwh - baseline_high_rate_kwh)
    low = max(Decimal("0"), current_low_rate_kwh - baseline_low_rate_kwh)

    elapsed_days = Decimal((as_of - cycle_start).days + 1)
    total_days = Decimal((settlement_date - cycle_start).days + 1)
    annual_fixed = prices.fixed_monthly_czk * Decimal("12")
    accrued_fixed = annual_fixed * elapsed_days / Decimal("365")
    projected_fixed = annual_fixed * total_days / Decimal("365")

    energy_cost = (
        high * prices.high_rate_czk_per_kwh
        + low * prices.low_rate_czk_per_kwh
    )
    projection_factor = total_days / elapsed_days
    projected_energy = energy_cost * projection_factor

    return CostProjection(
        high_rate_consumption_kwh=_energy(high),
        low_rate_consumption_kwh=_energy(low),
        accrued_energy_cost_czk=_money(energy_cost),
        accrued_fixed_cost_czk=_money(accrued_fixed),
        accrued_total_cost_czk=_money(energy_cost + accrued_fixed),
        projected_total_cost_czk=_money(projected_energy + projected_fixed),
    )


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _energy(value: Decimal) -> Decimal:
    return value.quantize(ENERGY_QUANT, rounding=ROUND_HALF_UP)
