from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Iterable

DEFAULT_VAT_RATE_PERCENT = Decimal("21")


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


def _validated_nonnegative(value: Decimal, field: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise ValueError(f"{field} must be Decimal")
    if not value.is_finite() or value < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return value


def _gross(value: Decimal, *, includes_vat: bool, vat_rate_percent: Decimal) -> Decimal:
    if includes_vat:
        return value
    return value * (Decimal("1") + vat_rate_percent / Decimal("100"))


@dataclass(frozen=True, slots=True)
class VariablePriceComponent:
    kind: PriceComponentKind
    name: str
    high_rate_czk_per_kwh: Decimal
    low_rate_czk_per_kwh: Decimal
    includes_vat: bool = True
    vat_rate_percent: Decimal = DEFAULT_VAT_RATE_PERCENT

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Variable price component name must not be empty")
        if not isinstance(self.includes_vat, bool):
            raise ValueError("includes_vat must be boolean")
        _validated_nonnegative(self.high_rate_czk_per_kwh, "high_rate_czk_per_kwh")
        _validated_nonnegative(self.low_rate_czk_per_kwh, "low_rate_czk_per_kwh")
        _validated_nonnegative(self.vat_rate_percent, "vat_rate_percent")

    @property
    def gross_high_rate_czk_per_kwh(self) -> Decimal:
        return _gross(
            self.high_rate_czk_per_kwh,
            includes_vat=self.includes_vat,
            vat_rate_percent=self.vat_rate_percent,
        )

    @property
    def gross_low_rate_czk_per_kwh(self) -> Decimal:
        return _gross(
            self.low_rate_czk_per_kwh,
            includes_vat=self.includes_vat,
            vat_rate_percent=self.vat_rate_percent,
        )


@dataclass(frozen=True, slots=True)
class FixedPriceComponent:
    kind: PriceComponentKind
    name: str
    monthly_czk: Decimal
    includes_vat: bool = True
    vat_rate_percent: Decimal = DEFAULT_VAT_RATE_PERCENT

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Fixed price component name must not be empty")
        if not isinstance(self.includes_vat, bool):
            raise ValueError("includes_vat must be boolean")
        _validated_nonnegative(self.monthly_czk, "monthly_czk")
        _validated_nonnegative(self.vat_rate_percent, "vat_rate_percent")

    @property
    def gross_monthly_czk(self) -> Decimal:
        return _gross(
            self.monthly_czk,
            includes_vat=self.includes_vat,
            vat_rate_percent=self.vat_rate_percent,
        )


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
    def all_in_vt_czk_kwh(self) -> Decimal:
        return sum(
            (item.gross_high_rate_czk_per_kwh for item in self.variable_components),
            Decimal("0"),
        )

    @property
    def all_in_nt_czk_kwh(self) -> Decimal:
        return sum(
            (item.gross_low_rate_czk_per_kwh for item in self.variable_components),
            Decimal("0"),
        )

    @property
    def fixed_monthly_total_czk(self) -> Decimal:
        return sum((item.gross_monthly_czk for item in self.fixed_components), Decimal("0"))

    @property
    def high_rate_czk_per_kwh(self) -> Decimal:
        """Backward-compatible alias for the gross all-in VT price."""
        return self.all_in_vt_czk_kwh

    @property
    def low_rate_czk_per_kwh(self) -> Decimal:
        """Backward-compatible alias for the gross all-in NT price."""
        return self.all_in_nt_czk_kwh

    @property
    def fixed_monthly_czk(self) -> Decimal:
        """Backward-compatible alias for the gross fixed monthly total."""
        return self.fixed_monthly_total_czk

    def variable_breakdown(self) -> dict[str, dict[str, Decimal]]:
        return {
            item.name: {
                "vt_czk_per_kwh": item.gross_high_rate_czk_per_kwh,
                "nt_czk_per_kwh": item.gross_low_rate_czk_per_kwh,
            }
            for item in self.variable_components
        }

    def fixed_breakdown(self) -> dict[str, Decimal]:
        return {item.name: item.gross_monthly_czk for item in self.fixed_components}


def select_price_for_day(prices: Iterable[AllInTariffPrice], day: date) -> AllInTariffPrice:
    matches = [item for item in prices if item.source.applies_on(day)]
    if not matches:
        raise LookupError(f"No tariff price applies on {day.isoformat()}")
    return max(matches, key=lambda item: item.source.valid_from)
