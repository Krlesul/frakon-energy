"""Fail-closed billing price selection from confirmed customer all-in tariffs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping

from .all_in_authority import (
    OPTION_ALL_IN_TARIFF_AUTHORITIES,
    AllInTariffAuthorityMethod,
    all_in_tariff_authority_from_options,
)
from .all_in_catalog import (
    OPTION_ALL_IN_TARIFF_CATALOG,
    all_in_tariff_fingerprint,
    confirmed_all_in_tariff_for_context_from_options,
)
from .contracts import OPTION_ELECTRICITY_CONTRACTS, confirmed_contract_from_options
from .cost import TariffPrices

_NEW_TARIFF_OPTION_KEYS = frozenset(
    {
        OPTION_ELECTRICITY_CONTRACTS,
        OPTION_ALL_IN_TARIFF_CATALOG,
        OPTION_ALL_IN_TARIFF_AUTHORITIES,
    }
)


@dataclass(frozen=True, slots=True)
class BillingTariffSelection:
    """One billing price selection with explicit source and authority metadata."""

    prices: TariffPrices
    source: str
    all_in_tariff_fingerprint: str | None = None
    authority_method: AllInTariffAuthorityMethod | None = None
    supplier: str | None = None
    product_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.prices, TariffPrices):
            raise ValueError("prices must be TariffPrices")
        if self.source not in {"confirmed_all_in", "legacy_options"}:
            raise ValueError("unsupported billing tariff source")
        if self.source == "confirmed_all_in":
            if self.all_in_tariff_fingerprint is None:
                raise ValueError("confirmed all-in selection requires tariff fingerprint")
            if self.authority_method is None:
                raise ValueError("confirmed all-in selection requires authority method")
            if not isinstance(self.supplier, str) or not self.supplier.strip():
                raise ValueError("confirmed all-in selection requires supplier")
            if not isinstance(self.product_name, str) or not self.product_name.strip():
                raise ValueError("confirmed all-in selection requires product_name")
        elif any(
            value is not None
            for value in (
                self.all_in_tariff_fingerprint,
                self.authority_method,
                self.supplier,
                self.product_name,
            )
        ):
            raise ValueError("legacy selection must not claim all-in authority metadata")


def has_new_tariff_catalog(options: Mapping[str, Any]) -> bool:
    """Return whether the immutable customer tariff authority model is present.

    Presence of any new-model key is enough to disable legacy price fallback.
    A partially migrated or corrupt catalog must fail closed rather than silently
    reverting to stale manually configured prices.
    """

    if not isinstance(options, Mapping):
        raise ValueError("options must be a mapping")
    return any(key in options for key in _NEW_TARIFF_OPTION_KEYS)


def select_billing_tariff_prices(
    options: Mapping[str, Any],
    *,
    day: date,
    legacy_prices: TariffPrices | None = None,
) -> BillingTariffSelection:
    """Select exact billing prices for ``day``.

    New catalog state is authoritative and fail-closed: confirmed contract,
    matching confirmed all-in version and explicit authority metadata must all
    resolve exactly. Legacy prices are accepted only when none of the new tariff
    option keys exists.
    """

    if not isinstance(options, Mapping):
        raise ValueError("options must be a mapping")
    if not isinstance(day, date):
        raise ValueError("day must be a date")

    if not has_new_tariff_catalog(options):
        if not isinstance(legacy_prices, TariffPrices):
            raise LookupError("legacy billing prices are not available")
        return BillingTariffSelection(prices=legacy_prices, source="legacy_options")

    contract = confirmed_contract_from_options(options, day)
    all_in = confirmed_all_in_tariff_for_context_from_options(
        options,
        supplier=contract.supplier.value,
        product_name=contract.product_name,
        distribution_tariff=contract.distribution_tariff,
        breaker_code=contract.breaker.code,
        day=day,
    )
    fingerprint = all_in_tariff_fingerprint(all_in)
    authority = all_in_tariff_authority_from_options(options, fingerprint)
    assembly = all_in.assembly

    return BillingTariffSelection(
        prices=TariffPrices(
            high_rate_czk_per_kwh=assembly.all_in_vt_czk_kwh,
            low_rate_czk_per_kwh=assembly.all_in_nt_czk_kwh,
            fixed_monthly_czk=assembly.fixed_monthly_total_czk,
        ),
        source="confirmed_all_in",
        all_in_tariff_fingerprint=fingerprint,
        authority_method=authority.method,
        supplier=assembly.supplier,
        product_name=assembly.product_name,
    )
