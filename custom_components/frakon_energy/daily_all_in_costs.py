"""Daily customer electricity costs backed by confirmed all-in tariff history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, Mapping

from .billing_all_in_history import BillingTariffSegment, confirmed_all_in_billing_schedule

_MONEY = Decimal("0.01")
_ENERGY = Decimal("0.001")
_RATE = Decimal("0.000001")


def _decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as err:
        raise ValueError(f"{field} must be numeric") from err
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return parsed


def _day(value: Any) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value)
        except ValueError as err:
            raise ValueError("daily consumption day must be ISO-8601") from err
    raise ValueError("daily consumption day must be a date")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


def _energy(value: Decimal) -> Decimal:
    return value.quantize(_ENERGY, rounding=ROUND_HALF_UP)


def _rate(value: Decimal) -> Decimal:
    return value.quantize(_RATE, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class DailyAllInCost:
    """One measured daily VT/NT record priced with one confirmed tariff version."""

    day: date
    high_rate_kwh: Decimal
    low_rate_kwh: Decimal
    high_rate_czk_per_kwh: Decimal
    low_rate_czk_per_kwh: Decimal
    variable_cost_czk: Decimal
    all_in_tariff_fingerprint: str
    authority_method: str
    supplier: str
    product_name: str
    fixed_monthly_excluded: bool = True

    @property
    def total_kwh(self) -> Decimal:
        return _energy(self.high_rate_kwh + self.low_rate_kwh)

    def as_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "high_rate_kwh": str(_energy(self.high_rate_kwh)),
            "low_rate_kwh": str(_energy(self.low_rate_kwh)),
            "total_kwh": str(self.total_kwh),
            "high_rate_czk_per_kwh": str(_rate(self.high_rate_czk_per_kwh)),
            "low_rate_czk_per_kwh": str(_rate(self.low_rate_czk_per_kwh)),
            "variable_cost_czk": str(_money(self.variable_cost_czk)),
            "all_in_tariff_fingerprint": self.all_in_tariff_fingerprint,
            "authority_method": self.authority_method,
            "supplier": self.supplier,
            "product_name": self.product_name,
            "fixed_monthly_excluded": self.fixed_monthly_excluded,
        }


@dataclass(frozen=True, slots=True)
class _NormalizedDailyConsumption:
    day: date
    high_rate_kwh: Decimal
    low_rate_kwh: Decimal


def _normalize_consumption(value: Any) -> _NormalizedDailyConsumption:
    if isinstance(value, Mapping):
        raw_day = value.get("day")
        high = value.get("high_rate_kwh")
        low = value.get("low_rate_kwh")
    else:
        raw_day = getattr(value, "day", None)
        high = getattr(value, "high_rate_kwh", None)
        low = getattr(value, "low_rate_kwh", None)
    return _NormalizedDailyConsumption(
        day=_day(raw_day),
        high_rate_kwh=_decimal(high, "high_rate_kwh"),
        low_rate_kwh=_decimal(low, "low_rate_kwh"),
    )


def _segment_for_day(
    schedule: tuple[BillingTariffSegment, ...],
    day: date,
) -> BillingTariffSegment:
    matches = [
        segment
        for segment in schedule
        if segment.valid_from <= day <= segment.valid_to
    ]
    if len(matches) != 1:
        raise LookupError(
            f"daily all-in tariff schedule is not exact for {day.isoformat()}"
        )
    return matches[0]


def price_confirmed_daily_consumption(
    options: Mapping[str, Any],
    consumption: Iterable[Any],
) -> tuple[DailyAllInCost, ...]:
    """Price measured daily consumption with exact confirmed customer tariffs.

    Fixed monthly charges are intentionally excluded. They belong only to billing
    period totals and must never be spread into a displayed daily or per-kWh cost.
    Every returned day carries the immutable all-in fingerprint and explicit
    authority method that produced its VT/NT price.
    """

    if not isinstance(options, Mapping):
        raise ValueError("options must be a mapping")
    normalized = sorted(
        (_normalize_consumption(item) for item in consumption),
        key=lambda item: item.day,
    )
    if not normalized:
        return ()
    days = [item.day for item in normalized]
    if len(days) != len(set(days)):
        raise ValueError("daily consumption contains duplicate calendar days")

    schedule = confirmed_all_in_billing_schedule(
        options,
        start_date=normalized[0].day,
        end_date=normalized[-1].day,
    )
    result: list[DailyAllInCost] = []
    for item in normalized:
        segment = _segment_for_day(schedule, item.day)
        variable_cost = (
            item.high_rate_kwh * segment.prices.high_rate_czk_per_kwh
            + item.low_rate_kwh * segment.prices.low_rate_czk_per_kwh
        )
        result.append(
            DailyAllInCost(
                day=item.day,
                high_rate_kwh=item.high_rate_kwh,
                low_rate_kwh=item.low_rate_kwh,
                high_rate_czk_per_kwh=segment.prices.high_rate_czk_per_kwh,
                low_rate_czk_per_kwh=segment.prices.low_rate_czk_per_kwh,
                variable_cost_czk=_money(variable_cost),
                all_in_tariff_fingerprint=segment.all_in_tariff_fingerprint,
                authority_method=segment.authority_method.value,
                supplier=segment.supplier,
                product_name=segment.product_name,
            )
        )
    return tuple(result)


def summarize_daily_all_in_costs(
    records: Iterable[DailyAllInCost],
) -> dict[str, Any]:
    """Return variable-only totals without manufacturing an effective fixed price."""

    materialized = tuple(records)
    high = sum((item.high_rate_kwh for item in materialized), Decimal("0"))
    low = sum((item.low_rate_kwh for item in materialized), Decimal("0"))
    variable_cost = sum((item.variable_cost_czk for item in materialized), Decimal("0"))
    return {
        "days": len(materialized),
        "high_rate_kwh": str(_energy(high)),
        "low_rate_kwh": str(_energy(low)),
        "total_kwh": str(_energy(high + low)),
        "variable_cost_czk": str(_money(variable_cost)),
        "fixed_monthly_excluded": True,
    }
