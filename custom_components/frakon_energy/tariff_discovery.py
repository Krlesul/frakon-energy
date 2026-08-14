"""Bridge stored electricity contracts to verified supplier tariff discovery."""

from __future__ import annotations

from datetime import date

from .contracts import ElectricityContract, lookup_key
from .tariff_candidate_selection import TariffCandidateReviewItem, candidate_review_items
from .tariff_sources import (
    TariffAdapterRegistry,
    TariffDocumentCandidate,
    TariffSourceQuery,
)


def tariff_source_query_from_contract(
    contract: ElectricityContract,
    *,
    day: date,
) -> TariffSourceQuery:
    """Create the canonical supplier-discovery query from one contract version.

    Discovery is only meaningful while the immutable contract version applies.
    Confirmation is deliberately not required here because this bridge is also
    used by the pre-activation review wizard. Confirmation remains mandatory
    before any tariff becomes active elsewhere in the pricing pipeline.
    """
    if not isinstance(contract, ElectricityContract):
        raise ValueError("contract must be ElectricityContract")
    if not isinstance(day, date):
        raise ValueError("day must be a date")
    if not contract.applies_on(day):
        raise ValueError("contract does not apply on requested discovery day")

    key = lookup_key(contract, day)
    return TariffSourceQuery(
        supplier=key.supplier.value,
        product_name=key.product_name,
        distributor=key.distributor.value,
        contract_kind=key.contract_kind.value,
        distribution_tariff=key.distribution_tariff,
        breaker_code=key.breaker_code,
        valid_on=key.valid_on,
    )


async def async_discover_contract_tariff_candidates(
    contract: ElectricityContract,
    *,
    day: date,
    registry: TariffAdapterRegistry,
) -> tuple[TariffDocumentCandidate, ...]:
    """Discover official candidates for one exact stored contract version."""
    if not isinstance(registry, TariffAdapterRegistry):
        raise ValueError("registry must be TariffAdapterRegistry")
    query = tariff_source_query_from_contract(contract, day=day)
    return await registry.async_discover_verified(query)


async def async_discover_contract_tariff_review(
    contract: ElectricityContract,
    *,
    day: date,
    registry: TariffAdapterRegistry,
) -> tuple[TariffCandidateReviewItem, ...]:
    """Return UI-safe review records without download, parsing or activation."""
    candidates = await async_discover_contract_tariff_candidates(
        contract,
        day=day,
        registry=registry,
    )
    return candidate_review_items(candidates)
