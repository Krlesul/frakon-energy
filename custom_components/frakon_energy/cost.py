from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import StrEnum
from typing import Any

MONEY_QUANT = Decimal("0.01")
ENERGY_QUANT = Decimal("0.001")


class VariablePriceComponentKind(StrEnum):
    """Supported per-kWh electricity price component categories."""

    COMMODITY = "commodity"
    DISTRIBUTION = "distribution"
    POZE = "poze"
    SYSTEM_SERVICES = "system_services"
    ELECTRICITY_TAX = "electricity_tax"
    MARKET = "market"
    OTHER = "other"


class FixedPriceComponentKind(StrEnum):
    """Supported recurring monthly price component categories."""

    SUPPLIER_STANDING = "supplier_standing"
    BREAKER = "breaker"
    DISTRIBUTION_FIXED = "distribution_fixed"
    OTHER = "other"


def _nonnegative_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as err:
        raise ValueError(f"{field} must be numeric") from err
    if not number.is_finite() or number < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return number


def _gross(value: Decimal, *, vat_included: bool, vat_rate_percent: Decimal) -> Decimal:
    if vat_included:
        return value
    return value * (Decimal("1") + vat_rate_percent / Decimal("100"))


@dataclass(frozen=True, slots=True)
class VariablePriceComponent:
    """One immutable variable electricity price component for VT and NT."""

    kind: VariablePriceComponentKind
    high_rate_czk_per_kwh: Decimal
    low_rate_czk_per_kwh: Decimal
    vat_included: bool = True
    vat_rate_percent: Decimal = Decimal("21")
    label: str | None = None
    source_reference: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "high_rate_czk_per_kwh",
            _nonnegative_decimal(self.high_rate_czk_per_kwh, "high_rate_czk_per_kwh"),
        )
        object.__setattr__(
            self,
            "low_rate_czk_per_kwh",
            _nonnegative_decimal(self.low_rate_czk_per_kwh, "low_rate_czk_per_kwh"),
        )
        object.__setattr__(
            self,
            "vat_rate_percent",
            _nonnegative_decimal(self.vat_rate_percent, "vat_rate_percent"),
        )
        if not isinstance(self.vat_included, bool):
            raise ValueError("vat_included must be boolean")
        if self.label is not None and not self.label.strip():
            raise ValueError("label must not be blank")
        if self.source_reference is not None and not self.source_reference.strip():
            raise ValueError("source_reference must not be blank")

    @property
    def gross_high_rate_czk_per_kwh(self) -> Decimal:
        return _gross(
            self.high_rate_czk_per_kwh,
            vat_included=self.vat_included,
            vat_rate_percent=self.vat_rate_percent,
        )

    @property
    def gross_low_rate_czk_per_kwh(self) -> Decimal:
        return _gross(
            self.low_rate_czk_per_kwh,
            vat_included=self.vat_included,
            vat_rate_percent=self.vat_rate_percent,
        )


@dataclass(frozen=True, slots=True)
class FixedPriceComponent:
    """One immutable recurring monthly tariff component."""

    kind: FixedPriceComponentKind
    monthly_czk: Decimal
    vat_included: bool = True
    vat_rate_percent: Decimal = Decimal("21")
    label: str | None = None
    source_reference: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "monthly_czk", _nonnegative_decimal(self.monthly_czk, "monthly_czk"))
        object.__setattr__(
            self,
            "vat_rate_percent",
            _nonnegative_decimal(self.vat_rate_percent, "vat_rate_percent"),
        )
        if not isinstance(self.vat_included, bool):
            raise ValueError("vat_included must be boolean")
        if self.label is not None and not self.label.strip():
            raise ValueError("label must not be blank")
        if self.source_reference is not None and not self.source_reference.strip():
            raise ValueError("source_reference must not be blank")

    @property
    def gross_monthly_czk(self) -> Decimal:
        return _gross(
            self.monthly_czk,
            vat_included=self.vat_included,
            vat_rate_percent=self.vat_rate_percent,
        )


@dataclass(frozen=True, slots=True)
class TariffPrices:
    high_rate_czk_per_kwh: Decimal
    low_rate_czk_per_kwh: Decimal
    fixed_monthly_czk: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class TariffPriceBreakdown:
    """Confirmed all-in electricity pricing with fixed charges kept separate."""

    variable_components: tuple[VariablePriceComponent, ...]
    fixed_components: tuple[FixedPriceComponent, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "variable_components", tuple(self.variable_components))
        object.__setattr__(self, "fixed_components", tuple(self.fixed_components))
        if not self.variable_components:
            raise ValueError("at least one variable price component is required")
        if not all(isinstance(item, VariablePriceComponent) for item in self.variable_components):
            raise ValueError("variable_components contains an invalid item")
        if not all(isinstance(item, FixedPriceComponent) for item in self.fixed_components):
            raise ValueError("fixed_components contains an invalid item")

    @property
    def all_in_vt_czk_kwh(self) -> Decimal:
        return sum(
            (item.gross_high_rate_czk_per_kwh for item in self.variable_components),
            start=Decimal("0"),
        )

    @property
    def all_in_nt_czk_kwh(self) -> Decimal:
        return sum(
            (item.gross_low_rate_czk_per_kwh for item in self.variable_components),
            start=Decimal("0"),
        )

    @property
    def fixed_monthly_total_czk(self) -> Decimal:
        return sum(
            (item.gross_monthly_czk for item in self.fixed_components),
            start=Decimal("0"),
        )

    def to_tariff_prices(self) -> TariffPrices:
        """Bridge the component model into the existing billing calculator."""
        return TariffPrices(
            high_rate_czk_per_kwh=self.all_in_vt_czk_kwh,
            low_rate_czk_per_kwh=self.all_in_nt_czk_kwh,
            fixed_monthly_czk=self.fixed_monthly_total_czk,
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

    Cumulative meter readings are compared with the previous settlement
    baseline. Energy consumption is projected using the average daily
    consumption observed in the current billing cycle. Fixed monthly charges
    are prorated by calendar days using 12 months per year.
    """
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
