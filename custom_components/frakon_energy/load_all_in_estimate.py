"""All-in VT/NT cost scenarios for planned flexible loads."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping

from .billing_tariff_selection import select_billing_tariff_prices

_MONEY_QUANT = Decimal("0.01")
_RATE_QUANT = Decimal("0.000001")
_ENERGY_QUANT = Decimal("0.001")
_SECONDS_PER_HOUR = Decimal("3600")


def _aware_datetime(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as err:
            raise ValueError(f"{field} must be an ISO-8601 datetime") from err
    else:
        raise ValueError(f"{field} must be an ISO-8601 datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return parsed


def _positive_decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as err:
        raise ValueError(f"{field} must be numeric") from err
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{field} must be finite and positive")
    return parsed


def _day_segments(start: datetime, end: datetime) -> tuple[tuple[date, Decimal], ...]:
    """Split an aware interval into calendar-day elapsed hours."""

    if end <= start:
        raise ValueError("ends_at must be after starts_at")
    result: list[tuple[date, Decimal]] = []
    cursor = start
    while cursor < end:
        next_day = cursor.date() + timedelta(days=1)
        boundary = datetime.combine(next_day, time.min, tzinfo=cursor.tzinfo)
        segment_end = min(end, boundary)
        seconds = Decimal(str((segment_end - cursor).total_seconds()))
        if seconds <= 0:
            raise ValueError("planned load contains an invalid calendar-day segment")
        result.append((cursor.date(), seconds / _SECONDS_PER_HOUR))
        cursor = segment_end
    return tuple(result)


def _money(value: Decimal) -> float:
    return float(value.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP))


def _rate(value: Decimal) -> float:
    return float(value.quantize(_RATE_QUANT, rounding=ROUND_HALF_UP))


def _energy(value: Decimal) -> float:
    return float(value.quantize(_ENERGY_QUANT, rounding=ROUND_HALF_UP))


def build_confirmed_all_in_load_estimate(
    options: Mapping[str, Any],
    *,
    starts_at: datetime | str,
    ends_at: datetime | str,
    power_kw: Decimal | float | str,
) -> dict[str, Any]:
    """Return VT/NT cost scenarios using only confirmed all-in customer tariffs.

    The spot planner remains the optimization signal. This estimate is a separate
    customer-cost view. It intentionally excludes recurring fixed monthly charges
    because a single EV/boiler/appliance run must not absorb arbitrary billing
    period fees.
    """

    if not isinstance(options, Mapping):
        raise ValueError("options must be a mapping")
    start = _aware_datetime(starts_at, "starts_at")
    end = _aware_datetime(ends_at, "ends_at")
    power = _positive_decimal(power_kw, "power_kw")

    total_energy = Decimal("0")
    vt_cost = Decimal("0")
    nt_cost = Decimal("0")
    tariff_records: list[dict[str, Any]] = []

    for day, hours in _day_segments(start, end):
        selection = select_billing_tariff_prices(
            options,
            day=day,
            legacy_prices=None,
        )
        if selection.source != "confirmed_all_in":
            raise LookupError("confirmed all-in tariff is required for load cost estimate")
        if selection.all_in_tariff_fingerprint is None or selection.authority_method is None:
            raise LookupError("confirmed all-in tariff authority metadata is required")

        segment_energy = power * hours
        total_energy += segment_energy
        vt_cost += segment_energy * selection.prices.high_rate_czk_per_kwh
        nt_cost += segment_energy * selection.prices.low_rate_czk_per_kwh
        tariff_records.append(
            {
                "day": day.isoformat(),
                "energy_kwh": _energy(segment_energy),
                "all_in_tariff_fingerprint": selection.all_in_tariff_fingerprint,
                "authority_method": selection.authority_method.value,
                "supplier": selection.supplier,
                "product_name": selection.product_name,
                "vt_czk_kwh": _rate(selection.prices.high_rate_czk_per_kwh),
                "nt_czk_kwh": _rate(selection.prices.low_rate_czk_per_kwh),
            }
        )

    if total_energy <= 0:
        raise ValueError("planned load energy must be positive")

    return {
        "available": True,
        "source": "confirmed_all_in",
        "estimated_energy_kwh": _energy(total_energy),
        "vt_average_czk_kwh": _rate(vt_cost / total_energy),
        "nt_average_czk_kwh": _rate(nt_cost / total_energy),
        "vt_cost_czk": _money(vt_cost),
        "nt_cost_czk": _money(nt_cost),
        "fixed_monthly_excluded": True,
        "tariffs": tariff_records,
    }


def unavailable_all_in_load_estimate() -> dict[str, Any]:
    """Stable fail-closed payload when no exact confirmed all-in tariff resolves."""

    return {
        "available": False,
        "source": "confirmed_all_in",
        "fixed_monthly_excluded": True,
        "reason": "confirmed_customer_all_in_unavailable",
    }
