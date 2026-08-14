from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Iterable, Mapping

DEFAULT_VAT_RATE_PERCENT = Decimal("21")
TARIFF_PRICE_SCHEMA_VERSION = 1


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


def _decimal_from_value(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as err:
        raise ValueError(f"{field} must be numeric") from err
    return _validated_nonnegative(parsed, field)


def _date_from_value(value: Any, field: str) -> date:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 date")
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"{field} must be an ISO-8601 date") from err


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
        if not isinstance(self.kind, PriceComponentKind):
            raise ValueError("kind must be PriceComponentKind")
        if not isinstance(self.name, str) or not self.name.strip():
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "name": self.name,
            "high_rate_czk_per_kwh": str(self.high_rate_czk_per_kwh),
            "low_rate_czk_per_kwh": str(self.low_rate_czk_per_kwh),
            "includes_vat": self.includes_vat,
            "vat_rate_percent": str(self.vat_rate_percent),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> VariablePriceComponent:
        if not isinstance(value, Mapping):
            raise ValueError("variable price component must be an object")
        try:
            kind = PriceComponentKind(str(value["kind"]))
            name = value["name"]
            includes_vat = value.get("includes_vat", True)
        except (KeyError, ValueError) as err:
            raise ValueError("invalid variable price component") from err
        return cls(
            kind=kind,
            name=name,
            high_rate_czk_per_kwh=_decimal_from_value(
                value.get("high_rate_czk_per_kwh"), "high_rate_czk_per_kwh"
            ),
            low_rate_czk_per_kwh=_decimal_from_value(
                value.get("low_rate_czk_per_kwh"), "low_rate_czk_per_kwh"
            ),
            includes_vat=includes_vat,
            vat_rate_percent=_decimal_from_value(
                value.get("vat_rate_percent", DEFAULT_VAT_RATE_PERCENT), "vat_rate_percent"
            ),
        )


@dataclass(frozen=True, slots=True)
class FixedPriceComponent:
    kind: PriceComponentKind
    name: str
    monthly_czk: Decimal
    includes_vat: bool = True
    vat_rate_percent: Decimal = DEFAULT_VAT_RATE_PERCENT

    def __post_init__(self) -> None:
        if not isinstance(self.kind, PriceComponentKind):
            raise ValueError("kind must be PriceComponentKind")
        if not isinstance(self.name, str) or not self.name.strip():
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "name": self.name,
            "monthly_czk": str(self.monthly_czk),
            "includes_vat": self.includes_vat,
            "vat_rate_percent": str(self.vat_rate_percent),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FixedPriceComponent:
        if not isinstance(value, Mapping):
            raise ValueError("fixed price component must be an object")
        try:
            kind = PriceComponentKind(str(value["kind"]))
            name = value["name"]
            includes_vat = value.get("includes_vat", True)
        except (KeyError, ValueError) as err:
            raise ValueError("invalid fixed price component") from err
        return cls(
            kind=kind,
            name=name,
            monthly_czk=_decimal_from_value(value.get("monthly_czk"), "monthly_czk"),
            includes_vat=includes_vat,
            vat_rate_percent=_decimal_from_value(
                value.get("vat_rate_percent", DEFAULT_VAT_RATE_PERCENT), "vat_rate_percent"
            ),
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

    def __post_init__(self) -> None:
        if not isinstance(self.supplier, str) or not self.supplier.strip():
            raise ValueError("supplier must not be empty")
        if not isinstance(self.product, str) or not self.product.strip():
            raise ValueError("product must not be empty")
        if not isinstance(self.valid_from, date):
            raise ValueError("valid_from must be a date")
        if self.valid_to is not None:
            if not isinstance(self.valid_to, date):
                raise ValueError("valid_to must be a date")
            if self.valid_to < self.valid_from:
                raise ValueError("price validity end must not precede start")
        if self.document_date is not None and not isinstance(self.document_date, date):
            raise ValueError("document_date must be a date")
        if not isinstance(self.confirmed, bool):
            raise ValueError("confirmed must be boolean")

    def applies_on(self, day: date) -> bool:
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)

    def as_dict(self) -> dict[str, Any]:
        return {
            "supplier": self.supplier,
            "product": self.product,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to is not None else None,
            "source_url": self.source_url,
            "document_date": self.document_date.isoformat() if self.document_date is not None else None,
            "checksum": self.checksum,
            "confirmed": self.confirmed,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PriceSource:
        if not isinstance(value, Mapping):
            raise ValueError("price source must be an object")
        valid_to_raw = value.get("valid_to")
        document_date_raw = value.get("document_date")
        return cls(
            supplier=value.get("supplier"),
            product=value.get("product"),
            valid_from=_date_from_value(value.get("valid_from"), "valid_from"),
            valid_to=(
                _date_from_value(valid_to_raw, "valid_to")
                if valid_to_raw not in (None, "")
                else None
            ),
            source_url=value.get("source_url"),
            document_date=(
                _date_from_value(document_date_raw, "document_date")
                if document_date_raw not in (None, "")
                else None
            ),
            checksum=value.get("checksum"),
            confirmed=value.get("confirmed", False),
        )


@dataclass(frozen=True, slots=True)
class AllInTariffPrice:
    source: PriceSource
    variable_components: tuple[VariablePriceComponent, ...]
    fixed_components: tuple[FixedPriceComponent, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.source, PriceSource):
            raise ValueError("source must be PriceSource")
        object.__setattr__(self, "variable_components", tuple(self.variable_components))
        object.__setattr__(self, "fixed_components", tuple(self.fixed_components))
        if not all(isinstance(item, VariablePriceComponent) for item in self.variable_components):
            raise ValueError("variable_components contains an invalid item")
        if not all(isinstance(item, FixedPriceComponent) for item in self.fixed_components):
            raise ValueError("fixed_components contains an invalid item")

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

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TARIFF_PRICE_SCHEMA_VERSION,
            "source": self.source.as_dict(),
            "variable_components": [item.as_dict() for item in self.variable_components],
            "fixed_components": [item.as_dict() for item in self.fixed_components],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AllInTariffPrice:
        if not isinstance(value, Mapping):
            raise ValueError("tariff price must be an object")
        if value.get("schema_version") != TARIFF_PRICE_SCHEMA_VERSION:
            raise ValueError("unsupported tariff price schema version")
        source_raw = value.get("source")
        variable_raw = value.get("variable_components")
        fixed_raw = value.get("fixed_components")
        if not isinstance(source_raw, Mapping):
            raise ValueError("tariff price source must be an object")
        if not isinstance(variable_raw, list) or not isinstance(fixed_raw, list):
            raise ValueError("tariff price components must be lists")
        return cls(
            source=PriceSource.from_dict(source_raw),
            variable_components=tuple(VariablePriceComponent.from_dict(item) for item in variable_raw),
            fixed_components=tuple(FixedPriceComponent.from_dict(item) for item in fixed_raw),
        )


def select_price_for_day(prices: Iterable[AllInTariffPrice], day: date) -> AllInTariffPrice:
    matches = [item for item in prices if item.source.applies_on(day)]
    if not matches:
        raise LookupError(f"No tariff price applies on {day.isoformat()}")
    return max(matches, key=lambda item: item.source.valid_from)


def select_confirmed_price_for_day(
    prices: Iterable[AllInTariffPrice], day: date
) -> AllInTariffPrice:
    """Return the latest confirmed tariff version that applies on the requested day."""
    matches = [
        item
        for item in prices
        if item.source.confirmed and item.source.applies_on(day)
    ]
    if not matches:
        raise LookupError(f"No confirmed tariff price applies on {day.isoformat()}")
    return max(matches, key=lambda item: item.source.valid_from)
