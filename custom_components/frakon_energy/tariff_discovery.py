"""Bridge stored electricity contracts to verified supplier tariff discovery."""

from __future__ import annotations

from datetime import date

from .contracts import ElectricityContract, lookup_key
from .tariff_candidate_selection import TariffCandidateReviewItem, candidate_review_items
from .tariff_sources import (
    TariffAdapterRegistry,
    TariffDocumentCandidate,
    TariffSourceQuery,
    TariffSourceResolutionContext,
)


def tariff_source_query_from_contract(
    contract: ElectricityContract,
    *,
    day: date,
    source_context: TariffSourceResolutionContext | None = None,
) -> TariffSourceQuery:
    if not isinstance(contract, ElectricityContract):
        raise ValueError("contract must be ElectricityContract")
    if not isinstance(day, date):
        raise ValueError("day must be a date")
    if not contract.applies_on(day):
        raise ValueError("contract does not apply on requested discovery day")
    if source_context is None:
        source_context = TariffSourceResolutionContext()
    if not isinstance(source_context, TariffSourceResolutionContext):
        raise ValueError("source_context must be TariffSourceResolutionContext")
    key = lookup_key(contract, day)
    return TariffSourceQuery(
        supplier=key.supplier.value,
        product_name=key.product_name,
        distributor=key.distributor.value,
        contract_kind=key.contract_kind.value,
        distribution_tariff=key.distribution_tariff,
        breaker_code=key.breaker_code,
        valid_on=key.valid_on,
        source_context=source_context,
    )


async def async_discover_contract_tariff_candidates(
    contract: ElectricityContract,
    *,
    day: date,
    registry: TariffAdapterRegistry,
    source_context: TariffSourceResolutionContext | None = None,
) -> tuple[TariffDocumentCandidate, ...]:
    if not isinstance(registry, TariffAdapterRegistry):
        raise ValueError("registry must be TariffAdapterRegistry")
    query = tariff_source_query_from_contract(contract, day=day, source_context=source_context)
    return await registry.async_discover_verified(query)


async def async_discover_contract_tariff_review(
    contract: ElectricityContract,
    *,
    day: date,
    registry: TariffAdapterRegistry,
    source_context: TariffSourceResolutionContext | None = None,
) -> tuple[TariffCandidateReviewItem, ...]:
    candidates = await async_discover_contract_tariff_candidates(
        contract,
        day=day,
        registry=registry,
        source_context=source_context,
    )
    return candidate_review_items(candidates)
