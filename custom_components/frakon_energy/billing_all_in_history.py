"""Historical confirmed tariff schedule for billing projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping

from .all_in_authority import (
    AllInTariffAuthorityMethod,
    all_in_tariff_authorities_from_options,
)
from .all_in_catalog import (
    all_in_tariff_fingerprint,
    all_in_tariffs_from_options,
    select_confirmed_all_in_tariff_for_context,
)
from .contracts import contracts_from_options, select_confirmed_contract_for_day
from .cost import CostProjection, ENERGY_QUANT, MONEY_QUANT, TariffPrices

_DAYS_PER_YEAR = Decimal("365")
_MONTHS_PER_YEAR = Decimal("12")
SOURCE_CONFIRMED_ALL_IN = "confirmed_all_in"
SOURCE_CONFIRMED_LEGACY_HISTORY = "confirmed_legacy_history"
LEGACY_MANUAL_IMPORT = "legacy_manual_import"


@dataclass(frozen=True, slots=True)
class BillingTariffSegment:
    """One contiguous calendar period backed by one confirmed tariff version."""

    valid_from: date
    valid_to: date
    prices: TariffPrices
    all_in_tariff_fingerprint: str | None
    authority_method: AllInTariffAuthorityMethod | str
    supplier: str | None
    product_name: str | None
    legacy_tariff_fingerprint: str | None = None
    source: str = SOURCE_CONFIRMED_ALL_IN

    def __post_init__(self) -> None:
        if not isinstance(self.valid_from, date) or not isinstance(self.valid_to, date):
            raise ValueError("billing tariff segment boundaries must be dates")
        if self.valid_to < self.valid_from:
            raise ValueError("billing tariff segment end must not precede start")
        if not isinstance(self.prices, TariffPrices):
            raise ValueError("billing tariff segment prices must be TariffPrices")
        if self.source == SOURCE_CONFIRMED_ALL_IN:
            if not isinstance(self.all_in_tariff_fingerprint, str):
                raise ValueError("confirmed all-in segment requires all-in fingerprint")
            if self.legacy_tariff_fingerprint is not None:
                raise ValueError("confirmed all-in segment cannot claim legacy fingerprint")
            if not isinstance(self.authority_method, AllInTariffAuthorityMethod):
                raise ValueError("confirmed all-in segment requires all-in authority")
            if not isinstance(self.supplier, str) or not self.supplier.strip():
                raise ValueError("confirmed all-in segment requires supplier")
            if not isinstance(self.product_name, str) or not self.product_name.strip():
                raise ValueError("confirmed all-in segment requires product_name")
        elif self.source == SOURCE_CONFIRMED_LEGACY_HISTORY:
            if self.all_in_tariff_fingerprint is not None:
                raise ValueError("legacy segment cannot claim all-in fingerprint")
            if not isinstance(self.legacy_tariff_fingerprint, str):
                raise ValueError("legacy segment requires legacy fingerprint")
            if self.authority_method != LEGACY_MANUAL_IMPORT:
                raise ValueError("legacy segment requires legacy_manual_import authority")
            if self.supplier is not None or self.product_name is not None:
                raise ValueError("legacy segment cannot claim supplier product identity")
        else:
            raise ValueError("unsupported billing tariff segment source")

    @property
    def day_count(self) -> int:
        return (self.valid_to - self.valid_from).days + 1

    @property
    def tariff_fingerprint(self) -> str:
        fingerprint = (
            self.all_in_tariff_fingerprint
            if self.source == SOURCE_CONFIRMED_ALL_IN
            else self.legacy_tariff_fingerprint
        )
        if not isinstance(fingerprint, str):
            raise ValueError("billing tariff segment fingerprint is unavailable")
        return fingerprint


@dataclass(frozen=True, slots=True)
class HistoricalAllInCostProjection:
    """Linear consumption projection priced with the exact tariff valid each day."""

    cost: CostProjection
    segments: tuple[BillingTariffSegment, ...]
    method: str = "daily_confirmed_all_in_schedule_linear_consumption"

    @property
    def tariff_version_count(self) -> int:
        return len({segment.tariff_fingerprint for segment in self.segments})


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _energy(value: Decimal) -> Decimal:
    return value.quantize(ENERGY_QUANT, rounding=ROUND_HALF_UP)


def _inclusive_days(start: date, end: date) -> int:
    if end < start:
        return 0
    return (end - start).days + 1


def _segment_prices(assembly: object) -> TariffPrices:
    return TariffPrices(
        high_rate_czk_per_kwh=getattr(assembly, "all_in_vt_czk_kwh"),
        low_rate_czk_per_kwh=getattr(assembly, "all_in_nt_czk_kwh"),
        fixed_monthly_czk=getattr(assembly, "fixed_monthly_total_czk"),
    )


def _legacy_segment_for_missing_day(
    options: Mapping[str, Any],
    *,
    day: date,
    original_error: LookupError,
) -> BillingTariffSegment:
    """Resolve a confirmed legacy snapshot or preserve the original missing error."""

    # Kept lazy deliberately: all-in-only billing does not depend on migration
    # support, and isolated tariff tests can continue loading only their authority
    # graph.  The historical module is required only when an exact day is missing.
    from .legacy_tariff_history import (
        confirmed_legacy_tariff_from_options,
        legacy_tariff_fingerprint,
    )

    try:
        snapshot = confirmed_legacy_tariff_from_options(options, day)
    except LookupError:
        raise original_error
    return BillingTariffSegment(
        valid_from=day,
        valid_to=day,
        prices=TariffPrices(
            high_rate_czk_per_kwh=snapshot.high_rate_czk_per_kwh,
            low_rate_czk_per_kwh=snapshot.low_rate_czk_per_kwh,
            fixed_monthly_czk=snapshot.fixed_monthly_czk,
        ),
        all_in_tariff_fingerprint=None,
        legacy_tariff_fingerprint=legacy_tariff_fingerprint(snapshot),
        authority_method=snapshot.authority_method.value,
        supplier=None,
        product_name=None,
        source=SOURCE_CONFIRMED_LEGACY_HISTORY,
    )


def _all_in_or_legacy_segment_for_day(
    options: Mapping[str, Any],
    *,
    day: date,
    contracts: tuple[object, ...],
    all_in_items: tuple[object, ...],
    authority_by_fingerprint: Mapping[str, object],
) -> BillingTariffSegment:
    """Prefer exact all-in authority; use legacy only when exact coverage is absent."""

    try:
        contract = select_confirmed_contract_for_day(contracts, day)
    except LookupError as err:
        return _legacy_segment_for_missing_day(
            options,
            day=day,
            original_error=err,
        )

    try:
        item = select_confirmed_all_in_tariff_for_context(
            all_in_items,
            supplier=contract.supplier.value,
            product_name=contract.product_name,
            distribution_tariff=contract.distribution_tariff,
            breaker_code=contract.breaker.code,
            day=day,
        )
    except LookupError as err:
        return _legacy_segment_for_missing_day(
            options,
            day=day,
            original_error=err,
        )

    fingerprint = all_in_tariff_fingerprint(item)
    authority = authority_by_fingerprint.get(fingerprint)
    if authority is None:
        # Referential corruption in the new authority graph is never a legacy
        # fallback condition. Preserve the existing fail-closed behavior.
        raise LookupError(f"all-in tariff authority not found: {fingerprint}")
    return BillingTariffSegment(
        valid_from=day,
        valid_to=day,
        prices=_segment_prices(item.assembly),
        all_in_tariff_fingerprint=fingerprint,
        legacy_tariff_fingerprint=None,
        authority_method=authority.method,
        supplier=item.assembly.supplier,
        product_name=item.assembly.product_name,
        source=SOURCE_CONFIRMED_ALL_IN,
    )


def confirmed_all_in_billing_schedule(
    options: Mapping[str, Any],
    *,
    start_date: date,
    end_date: date,
) -> tuple[BillingTariffSegment, ...]:
    """Resolve exact confirmed pricing for every day and compress it.

    Confirmed new-model all-in pricing always wins. A confirmed legacy snapshot
    may fill only a day for which the exact confirmed contract/all-in lookup is
    absent. Ambiguous contracts, ambiguous all-in versions, missing authority or
    corrupt catalogs still fail closed and are never hidden by legacy history.
    """

    if not isinstance(options, Mapping):
        raise ValueError("options must be a mapping")
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        raise ValueError("billing schedule boundaries must be dates")
    if end_date < start_date:
        raise ValueError("billing schedule end must not precede start")

    contracts = contracts_from_options(options)
    all_in_items = all_in_tariffs_from_options(options)
    authorities = all_in_tariff_authorities_from_options(options)
    authority_by_fingerprint = {
        item.all_in_tariff_fingerprint: item for item in authorities
    }

    segments: list[BillingTariffSegment] = []
    cursor = start_date
    while cursor <= end_date:
        segment = _all_in_or_legacy_segment_for_day(
            options,
            day=cursor,
            contracts=contracts,
            all_in_items=all_in_items,
            authority_by_fingerprint=authority_by_fingerprint,
        )

        if (
            segments
            and segments[-1].source == segment.source
            and segments[-1].tariff_fingerprint == segment.tariff_fingerprint
            and segments[-1].authority_method == segment.authority_method
            and segments[-1].valid_to + timedelta(days=1) == cursor
        ):
            previous = segments[-1]
            segments[-1] = BillingTariffSegment(
                valid_from=previous.valid_from,
                valid_to=cursor,
                prices=previous.prices,
                all_in_tariff_fingerprint=previous.all_in_tariff_fingerprint,
                legacy_tariff_fingerprint=previous.legacy_tariff_fingerprint,
                authority_method=previous.authority_method,
                supplier=previous.supplier,
                product_name=previous.product_name,
                source=previous.source,
            )
        else:
            segments.append(segment)
        cursor += timedelta(days=1)

    if not segments:
        raise LookupError("confirmed billing tariff schedule is empty")
    return tuple(segments)


def calculate_confirmed_all_in_cost_projection(
    options: Mapping[str, Any],
    *,
    cycle_start: date,
    settlement_date: date,
    as_of: date,
    baseline_high_rate_kwh: Decimal,
    baseline_low_rate_kwh: Decimal,
    current_high_rate_kwh: Decimal,
    current_low_rate_kwh: Decimal,
) -> HistoricalAllInCostProjection:
    """Price a linear VT/NT consumption projection with historical tariff versions.

    The existing billing model assumes observed consumption is uniformly spread
    across elapsed calendar days. This function preserves that assumption, but it
    prices each elapsed and future day with the exact confirmed tariff that applies
    on that day. New confirmed all-in versions remain authoritative; explicitly
    confirmed legacy snapshots may only fill pre-catalog historical gaps.
    """

    if settlement_date < cycle_start:
        raise ValueError("Settlement date must not precede cycle start")
    if not cycle_start <= as_of <= settlement_date:
        raise ValueError("as_of must fall inside the billing cycle")

    high = max(Decimal("0"), current_high_rate_kwh - baseline_high_rate_kwh)
    low = max(Decimal("0"), current_low_rate_kwh - baseline_low_rate_kwh)
    elapsed_days = Decimal(_inclusive_days(cycle_start, as_of))
    if elapsed_days <= 0:
        raise ValueError("billing cycle must contain at least one elapsed day")
    average_high_per_day = high / elapsed_days
    average_low_per_day = low / elapsed_days

    segments = confirmed_all_in_billing_schedule(
        options,
        start_date=cycle_start,
        end_date=settlement_date,
    )

    accrued_energy = Decimal("0")
    accrued_fixed = Decimal("0")
    projected_energy = Decimal("0")
    projected_fixed = Decimal("0")

    for segment in segments:
        total_days = Decimal(segment.day_count)
        daily_energy_cost = (
            average_high_per_day * segment.prices.high_rate_czk_per_kwh
            + average_low_per_day * segment.prices.low_rate_czk_per_kwh
        )
        daily_fixed_cost = (
            segment.prices.fixed_monthly_czk * _MONTHS_PER_YEAR / _DAYS_PER_YEAR
        )
        projected_energy += daily_energy_cost * total_days
        projected_fixed += daily_fixed_cost * total_days

        elapsed_end = min(segment.valid_to, as_of)
        elapsed_segment_days = Decimal(
            _inclusive_days(segment.valid_from, elapsed_end)
        )
        if elapsed_segment_days > 0:
            accrued_energy += daily_energy_cost * elapsed_segment_days
            accrued_fixed += daily_fixed_cost * elapsed_segment_days

    cost = CostProjection(
        high_rate_consumption_kwh=_energy(high),
        low_rate_consumption_kwh=_energy(low),
        accrued_energy_cost_czk=_money(accrued_energy),
        accrued_fixed_cost_czk=_money(accrued_fixed),
        accrued_total_cost_czk=_money(accrued_energy + accrued_fixed),
        projected_total_cost_czk=_money(projected_energy + projected_fixed),
    )
    method = (
        "daily_confirmed_all_in_schedule_linear_consumption"
        if all(segment.source == SOURCE_CONFIRMED_ALL_IN for segment in segments)
        else "daily_confirmed_mixed_tariff_history_linear_consumption"
    )
    return HistoricalAllInCostProjection(
        cost=cost,
        segments=segments,
        method=method,
    )
