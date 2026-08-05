from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Iterable


class PriceComponentKind(StrEnum):
    COMMODITY = "commodity"
    DISTRIBUTION = "distribution"
    POZE = "poze"
    SYSTEM_SERVICES = "system_services"
    ELECTRICITY_TAX = "electricity_tax"
    MARKET = "market"
    OTHER_VARIABLE = "other_variable"
    SUPPLIER_FIXED = "supplier_fixed"
    BREAKER_FIXED = "breaker_fixed"
    DISTRIBUTION_FIXED = "distribution_fixed"
    OTHER_FIXED = "other_fixed"


@dataclass(frozen=True, slots=True)
class VariablePriceComponent:
    kind: PriceComponentKind
    name: str
    high_rate_czk_per_kwh: Decimal
    low_rate_czk_per_kwh: Decimal
    includes_vat: bool = True

    def __post_init__(self) -> None:
        if self.high_rate_czk_per_kwh < 0 or self.low_rate_czk_per_kwh < 0:
            raise ValueError("Variable price components must not be negative")


@dataclass(frozen=True, slots=True)
class FixedPriceComponent:
    kind: PriceComponentKind
    name: str
    monthly_czk: Decimal
    includes_vat: bool = True

    def __post_init__(self) -> None:
        if self.monthly_czk < 0:
            raise ValueError("Fixed price components must not be negative")


@dataclass(frozen=True, slots=True)
class PriceSource:
    supplier: str
    product: str
    valid_from: date
    valid_to: date | None = None
    source_url: str | None = None
    document_date: date | None = None
    checksum: str | None = None
    confirmed: bool = False

    def applies_on(self, day: date) -> bool:
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)


@dataclass(frozen=True, slots=True)
class AllInTariffPrice:
    source: PriceSource
    variable_components: tuple[VariablePriceComponent, ...]
    fixed_components: tuple[FixedPriceComponent, ...]

    @property
    def high_rate_czk_per_kwh(self) -> Decimal:
        return sum((item.high_rate_czk_per_kwh for item in self.variable_components), Decimal("0"))

    @property
    def low_rate_czk_per_kwh(self) -> Decimal:
        return sum((item.low_rate_czk_per_kwh for item in self.variable_components), Decimal("0"))

    @property
    def fixed_monthly_czk(self) -> Decimal:
        return sum((item.monthly_czk for item in self.fixed_components), Decimal("0"))

    def variable_breakdown(self) -> dict[str, dict[str, Decimal]]:
        return {
            item.name: {
                "vt_czk_per_kwh": item.high_rate_czk_per_kwh,
                "nt_czk_per_kwh": item.low_rate_czk_per_kwh,
            }
            for item in self.variable_components
        }

    def fixed_breakdown(self) -> dict[str, Decimal]:
        return {item.name: item.monthly_czk for item in self.fixed_components}


def select_price_for_day(prices: Iterable[AllInTariffPrice], day: date) -> AllInTariffPrice:
    matches = [item for item in prices if item.source.applies_on(day)]
    if not matches:
        raise LookupError(f"No tariff price applies on {day.isoformat()}")
    return max(matches, key=lambda item: item.source.valid_from)
