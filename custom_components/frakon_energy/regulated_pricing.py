"""Verified regulated electricity-price components for FRAKON Energy.

This module intentionally supports partial regulated bundles. A partial bundle
must never be treated as an all-in customer tariff until every required
regulated/statutory component is supplied by separately verified sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .pricing import PriceComponentKind, VariablePriceComponent
from .tariff_sources import PRICE_SCOPE_REGULATED

ERU_CV_15_2025_URL = "https://eru.gov.cz/kopie-z-energeticky-regulacni-vestnik-192025"
ERU_CV_15_2025 = "Cenový výměr ERÚ č. 15/2025"
POZE_2026_VALID_FROM = date(2026, 1, 1)
POZE_2026_VALID_TO = date(2026, 12, 31)

# These categories are still required before a 2026 low-voltage household
# tariff can be called complete by this module. They are deliberately named
# rather than filled with guessed or stale values.
REGULATED_REQUIRED_BEFORE_ALL_IN = (
    PriceComponentKind.DISTRIBUTION,
    PriceComponentKind.SYSTEM_SERVICES,
    PriceComponentKind.MARKET,
    PriceComponentKind.BREAKER_FIXED,
    PriceComponentKind.DISTRIBUTION_FIXED,
)


@dataclass(frozen=True, slots=True)
class RegulatorySource:
    """Immutable provenance for one verified regulatory rule/value set."""

    authority: str
    legal_basis: str
    source_url: str
    published_on: date

    def __post_init__(self) -> None:
        if not isinstance(self.authority, str) or not self.authority.strip():
            raise ValueError("authority must not be empty")
        if not isinstance(self.legal_basis, str) or not self.legal_basis.strip():
            raise ValueError("legal_basis must not be empty")
        if not isinstance(self.source_url, str) or not self.source_url.startswith("https://"):
            raise ValueError("source_url must use HTTPS")
        if not isinstance(self.published_on, date):
            raise ValueError("published_on must be a date")


@dataclass(frozen=True, slots=True)
class PartialRegulatedPriceBundle:
    """A verified but explicitly incomplete set of regulated components."""

    valid_from: date
    valid_to: date
    source: RegulatorySource
    variable_components: tuple[VariablePriceComponent, ...]
    missing_component_kinds: tuple[PriceComponentKind, ...]
    price_scope: str = field(default=PRICE_SCOPE_REGULATED, init=False)
    all_in_ready: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.valid_from, date) or not isinstance(self.valid_to, date):
            raise ValueError("regulated bundle validity must use dates")
        if self.valid_to < self.valid_from:
            raise ValueError("regulated bundle validity end must not precede start")
        if not isinstance(self.source, RegulatorySource):
            raise ValueError("source must be RegulatorySource")
        variable = tuple(self.variable_components)
        missing = tuple(self.missing_component_kinds)
        if not variable:
            raise ValueError("regulated bundle must contain at least one verified component")
        if not all(isinstance(item, VariablePriceComponent) for item in variable):
            raise ValueError("variable_components contains an invalid item")
        if not missing:
            raise ValueError("partial regulated bundle must name missing components")
        if not all(isinstance(item, PriceComponentKind) for item in missing):
            raise ValueError("missing_component_kinds contains an invalid item")
        present = {item.kind for item in variable}
        if present.intersection(missing):
            raise ValueError("a component cannot be both present and missing")
        object.__setattr__(self, "variable_components", variable)
        object.__setattr__(self, "missing_component_kinds", missing)

    def applies_on(self, day: date) -> bool:
        if not isinstance(day, date):
            raise ValueError("day must be a date")
        return self.valid_from <= day <= self.valid_to

    def component(self, kind: PriceComponentKind) -> VariablePriceComponent:
        matches = [item for item in self.variable_components if item.kind == kind]
        if len(matches) != 1:
            raise LookupError(f"regulated component not uniquely available: {kind.value}")
        return matches[0]


POZE_2026_SOURCE = RegulatorySource(
    authority="Energetický regulační úřad",
    legal_basis=ERU_CV_15_2025,
    source_url=ERU_CV_15_2025_URL,
    published_on=date(2025, 12, 29),
)

POZE_2026_COMPONENT = VariablePriceComponent(
    kind=PriceComponentKind.POZE,
    name="POZE 2026 – hrazeno státem",
    high_rate_czk_per_kwh=__import__("decimal").Decimal("0"),
    low_rate_czk_per_kwh=__import__("decimal").Decimal("0"),
    includes_vat=True,
)

POZE_2026_REGULATED_BASE = PartialRegulatedPriceBundle(
    valid_from=POZE_2026_VALID_FROM,
    valid_to=POZE_2026_VALID_TO,
    source=POZE_2026_SOURCE,
    variable_components=(POZE_2026_COMPONENT,),
    missing_component_kinds=REGULATED_REQUIRED_BEFORE_ALL_IN,
)


def regulated_base_for_day(day: date) -> PartialRegulatedPriceBundle:
    """Return the verified partial regulated base for the requested day.

    Only 2026 is currently encoded. Failing closed outside that period prevents
    a historical or future POZE rule from being silently reused.
    """
    if POZE_2026_REGULATED_BASE.applies_on(day):
        return POZE_2026_REGULATED_BASE
    raise LookupError(f"no verified regulated base is available for {day.isoformat()}")
