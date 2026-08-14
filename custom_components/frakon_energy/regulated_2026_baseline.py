"""Universal 2026 regulated electricity components with official provenance.

Only charges that are universal and directly confirmed by official ERÚ/OTE
sources are frozen here. Distribution VT/NT, breaker and system-service values
remain separate parser inputs because they are table- and amendment-dependent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .pricing import FixedPriceComponent, PriceComponentKind, VariablePriceComponent
from .regulated_pricing import NON_NETWORK_INFRASTRUCTURE_COMPONENT_NAME
from .tariff_provenance import PriceEvidence, PriceSourceType
from .tariff_sources import PRICE_SCOPE_REGULATED

VALID_FROM_2026 = date(2026, 1, 1)
POZE_2026_CZK_PER_KWH = Decimal("0")
OTE_NON_NETWORK_2026_CZK_PER_MONTH = Decimal("12.87")

ERU_POZE_2026_SOURCE_URL = "https://eru.gov.cz/kopie-z-energeticky-regulacni-vestnik-192025"
OTE_2026_SERVICE_PRICE_URL = (
    "https://www.ote-cr.cz/cs/registrace-a-smlouvy/smluvni-vztahy-elektrina/"
    "ceny-za-sluzby-ote"
)


@dataclass(frozen=True, slots=True)
class UniversalRegulated2026Baseline:
    """Source-anchored universal regulated components, never a full tariff."""

    valid_from: date
    variable_components: tuple[VariablePriceComponent, ...]
    fixed_components: tuple[FixedPriceComponent, ...]
    evidence: tuple[PriceEvidence, ...]
    price_scope: str = field(default=PRICE_SCOPE_REGULATED, init=False)
    all_in_ready: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.valid_from != VALID_FROM_2026:
            raise ValueError("2026 regulated baseline must start on 2026-01-01")
        variable = tuple(self.variable_components)
        fixed = tuple(self.fixed_components)
        evidence = tuple(self.evidence)
        if len(variable) != 1 or variable[0].kind != PriceComponentKind.POZE:
            raise ValueError("2026 universal variable baseline must contain only POZE")
        if (
            len(fixed) != 1
            or fixed[0].kind != PriceComponentKind.OTHER_FIXED
            or fixed[0].name != NON_NETWORK_INFRASTRUCTURE_COMPONENT_NAME
        ):
            raise ValueError("2026 universal fixed baseline must contain only non-network infrastructure")
        if len(evidence) != 2 or any(not isinstance(item, PriceEvidence) for item in evidence):
            raise ValueError("2026 universal baseline requires ERÚ and OTE evidence")
        if any(item.scope != PRICE_SCOPE_REGULATED for item in evidence):
            raise ValueError("2026 baseline evidence must use regulated scope")
        object.__setattr__(self, "variable_components", variable)
        object.__setattr__(self, "fixed_components", fixed)
        object.__setattr__(self, "evidence", evidence)


def universal_regulated_2026_baseline() -> UniversalRegulated2026Baseline:
    """Build confirmed universal 2026 POZE and OTE components without guessing tables."""
    poze = VariablePriceComponent(
        kind=PriceComponentKind.POZE,
        name="POZE",
        high_rate_czk_per_kwh=POZE_2026_CZK_PER_KWH,
        low_rate_czk_per_kwh=POZE_2026_CZK_PER_KWH,
        includes_vat=False,
    )
    non_network = FixedPriceComponent(
        kind=PriceComponentKind.OTHER_FIXED,
        name=NON_NETWORK_INFRASTRUCTURE_COMPONENT_NAME,
        monthly_czk=OTE_NON_NETWORK_2026_CZK_PER_MONTH,
        includes_vat=False,
    )
    evidence = (
        PriceEvidence(
            source_type=PriceSourceType.OFFICIAL_PRICE_LIST,
            scope=PRICE_SCOPE_REGULATED,
            source_name="Energetický regulační úřad",
            document_name="Cenový výměr 15/2025 – změna regulovaných cen pro rok 2026",
            valid_from=VALID_FROM_2026,
            source_url=ERU_POZE_2026_SOURCE_URL,
            confirmed=True,
        ),
        PriceEvidence(
            source_type=PriceSourceType.OFFICIAL_PRICE_LIST,
            scope=PRICE_SCOPE_REGULATED,
            source_name="OTE, a.s.",
            document_name="Ceny za služby operátora trhu v elektroenergetice 2026",
            valid_from=VALID_FROM_2026,
            source_url=OTE_2026_SERVICE_PRICE_URL,
            confirmed=True,
        ),
    )
    return UniversalRegulated2026Baseline(
        valid_from=VALID_FROM_2026,
        variable_components=(poze,),
        fixed_components=(non_network,),
        evidence=evidence,
    )
