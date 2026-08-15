"""Resolve the effective VT/NT and fixed prices used by billing consumers.

Confirmed customer all-in pricing is authoritative. Legacy billing price options
are retained only as a migration fallback when no confirmed customer all-in
version is available for the requested day. Ambiguous or malformed confirmed
state is never hidden by that fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Mapping

from .all_in_catalog import (
    all_in_tariff_fingerprint,
    confirmed_all_in_tariff_for_context_from_options,
)
from .contracts import confirmed_contract_from_options
from .cost import TariffPrices

LEGACY_PRICE_VT_KEY = "price_vt_czk_kwh"
LEGACY_PRICE_NT_KEY = "price_nt_czk_kwh"
LEGACY_FIXED_MONTHLY_KEY = "fixed_monthly_czk"


class BillingTariffSource(StrEnum):
    CONFIRMED_ALL_IN = "confirmed_all_in"
    LEGACY_OPTIONS = "legacy_options"


def _nonnegative_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field} must be a finite non-negative number")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as err:
        raise ValueError(f"{field} must be a finite non-negative number") from err
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return parsed


@dataclass(frozen=True, slots=True)
class BillingTariffSelection:
    """Effective billing prices plus immutable authority metadata."""

    prices: TariffPrices
    source: BillingTariffSource
    all_in_tariff_fingerprint: str | None = None
    supplier: str | None = None
    product_name: str | None = None
    distribution_tariff: str | None = None
    breaker_code: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.prices, TariffPrices):
            raise ValueError("prices must be TariffPrices")
        if not isinstance(self.source, BillingTariffSource):
            raise ValueError("source must be BillingTariffSource")
        if self.source is BillingTariffSource.CONFIRMED_ALL_IN:
            if (
                not isinstance(self.all_in_tariff_fingerprint, str)
                or len(self.all_in_tariff_fingerprint) != 64
                or any(
                    char not in "0123456789abcdef"
                    for char in self.all_in_tariff_fingerprint
                )
            ):
                raise ValueError(
                    "confirmed all-in billing selection requires a tariff fingerprint"
                )
            for value, field in (
                (self.supplier, "supplier"),
                (self.product_name, "product_name"),
                (self.distribution_tariff, "distribution_tariff"),
                (self.breaker_code, "breaker_code"),
            ):
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"confirmed all-in billing selection requires {field}"
                    )
            if not isinstance(self.valid_from, date):
                raise ValueError(
                    "confirmed all-in billing selection requires valid_from"
                )

    @property
    def uses_confirmed_all_in(self) -> bool:
        return self.source is BillingTariffSource.CONFIRMED_ALL_IN


def _legacy_selection(options: Mapping[str, Any]) -> BillingTariffSelection:
    missing = [
        key
        for key in (
            LEGACY_PRICE_VT_KEY,
            LEGACY_PRICE_NT_KEY,
            LEGACY_FIXED_MONTHLY_KEY,
        )
        if key not in options
    ]
    if missing:
        raise LookupError(
            "No confirmed all-in tariff applies and legacy billing prices are "
            f"incomplete: {', '.join(missing)}"
        )
    return BillingTariffSelection(
        prices=TariffPrices(
            high_rate_czk_per_kwh=_nonnegative_decimal(
                options[LEGACY_PRICE_VT_KEY], LEGACY_PRICE_VT_KEY
            ),
            low_rate_czk_per_kwh=_nonnegative_decimal(
                options[LEGACY_PRICE_NT_KEY], LEGACY_PRICE_NT_KEY
            ),
            fixed_monthly_czk=_nonnegative_decimal(
                options[LEGACY_FIXED_MONTHLY_KEY], LEGACY_FIXED_MONTHLY_KEY
            ),
        ),
        source=BillingTariffSource.LEGACY_OPTIONS,
    )


def billing_tariff_selection_for_day(
    options: Mapping[str, Any],
    day: date,
) -> BillingTariffSelection:
    """Return customer billing prices for ``day`` with all-in authority first.

    ``LookupError`` means the required confirmed customer context is absent and
    therefore permits the explicit legacy migration fallback. ``ValueError`` is
    intentionally not caught: ambiguity or corrupt confirmed data must fail
    closed instead of silently reverting to manually configured legacy prices.
    """
    if not isinstance(options, Mapping):
        raise ValueError("options must be a mapping")
    if not isinstance(day, date):
        raise ValueError("day must be a date")

    try:
        contract = confirmed_contract_from_options(options, day)
    except LookupError:
        return _legacy_selection(options)

    try:
        item = confirmed_all_in_tariff_for_context_from_options(
            options,
            supplier=contract.supplier.value,
            product_name=contract.product_name,
            distribution_tariff=contract.distribution_tariff,
            breaker_code=contract.breaker.code,
            day=day,
        )
    except LookupError:
        return _legacy_selection(options)

    assembly = item.assembly
    return BillingTariffSelection(
        prices=TariffPrices(
            high_rate_czk_per_kwh=assembly.all_in_vt_czk_kwh,
            low_rate_czk_per_kwh=assembly.all_in_nt_czk_kwh,
            fixed_monthly_czk=assembly.fixed_monthly_total_czk,
        ),
        source=BillingTariffSource.CONFIRMED_ALL_IN,
        all_in_tariff_fingerprint=all_in_tariff_fingerprint(item),
        supplier=assembly.supplier,
        product_name=assembly.product_name,
        distribution_tariff=assembly.distribution_tariff,
        breaker_code=assembly.breaker_code,
        valid_from=assembly.valid_from,
        valid_to=assembly.valid_to,
    )
