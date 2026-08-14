"""Fail-closed regulated electricity price component contracts.

This module intentionally does not parse ERÚ tables. It defines the typed
boundary that verified ERÚ/OTE parsers must satisfy before regulated values can
join supplier-commercial pricing. All monetary inputs are expected to use the
official source convention (CZK excluding VAT) and are converted to existing
FRAKON pricing components without claiming a complete all-in tariff.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
import re
from urllib.parse import urlsplit

from .pricing import FixedPriceComponent, PriceComponentKind, VariablePriceComponent
from .tariff_sources import PRICE_SCOPE_REGULATED

_DISTRIBUTION_TARIFF_RE = re.compile(r"^D\d{2}d$")
_BREAKER_RE = re.compile(r"^(?:1|3)x[1-9]\d*A$")

# Universal customer charges confirmed by official 2026 regulator/operator
# publications. Distribution, breaker and system-service values stay parser
# inputs because they depend on tables/amendments and must not be guessed.
POZE_2026_CZK_PER_KWH = Decimal("0")
OTE_NON_NETWORK_2026_CZK_PER_MONTH = Decimal("12.87")


class RegulatedAuthority(StrEnum):
    ERU = "eru"
    OTE = "ote"


_OFFICIAL_DOMAINS = {
    RegulatedAuthority.ERU: "eru.gov.cz",
    RegulatedAuthority.OTE: "ote-cr.cz",
}


@dataclass(frozen=True, slots=True)
class RegulatedPriceSource:
    """Metadata for one official regulatory source used by a parsed value set."""

    authority: RegulatedAuthority
    document_id: str
    source_url: str
    valid_from: date
    valid_to: date | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.authority, RegulatedAuthority):
            raise ValueError("authority must be RegulatedAuthority")
        if not isinstance(self.document_id, str) or not self.document_id.strip():
            raise ValueError("document_id must not be empty")
        if not isinstance(self.valid_from, date):
            raise ValueError("valid_from must be a date")
        if self.valid_to is not None:
            if not isinstance(self.valid_to, date):
                raise ValueError("valid_to must be a date")
            if self.valid_to < self.valid_from:
                raise ValueError("source validity end must not precede start")

        try:
            parsed = urlsplit(self.source_url)
            port = parsed.port
        except (TypeError, ValueError) as err:
            raise ValueError("source_url must be a valid HTTPS URL") from err
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise ValueError("source_url must use HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("source_url must not contain credentials")
        if port not in (None, 443):
            raise ValueError("source_url must use the standard HTTPS port")
        official = _OFFICIAL_DOMAINS[self.authority]
        host = parsed.hostname.lower().rstrip(".")
        if host != official and not host.endswith(f".{official}"):
            raise ValueError("source_url is outside the authority's official domain")

    def applies_on(self, day: date) -> bool:
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)


@dataclass(frozen=True, slots=True)
class RegulatedTariffComponents:
    """Verified regulated inputs for one distributor/tariff/breaker combination.

    Values are CZK excluding VAT, matching ERÚ/OTE publication convention.
    This object is deliberately partial: it never contains supplier commodity
    prices or electricity tax and therefore can never be treated as a complete
    customer all-in tariff by itself.
    """

    distributor: str
    distribution_tariff: str
    breaker_code: str
    valid_from: date
    distribution_vt_czk_per_kwh: Decimal
    distribution_nt_czk_per_kwh: Decimal
    breaker_monthly_czk: Decimal
    system_services_czk_per_kwh: Decimal
    poze_czk_per_kwh: Decimal
    non_network_monthly_czk: Decimal
    sources: tuple[RegulatedPriceSource, ...]
    price_scope: str = field(default=PRICE_SCOPE_REGULATED, init=False)
    all_in_ready: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.distributor, str) or not self.distributor.strip():
            raise ValueError("distributor must not be empty")
        tariff = self.distribution_tariff.strip() if isinstance(self.distribution_tariff, str) else ""
        tariff = tariff[0].upper() + tariff[1:-1] + tariff[-1].lower() if tariff else ""
        if not _DISTRIBUTION_TARIFF_RE.fullmatch(tariff):
            raise ValueError("distribution_tariff must use a code such as D25d")
        object.__setattr__(self, "distribution_tariff", tariff)
        if not isinstance(self.breaker_code, str) or not _BREAKER_RE.fullmatch(self.breaker_code):
            raise ValueError("breaker_code must use a code such as 3x25A")
        if not isinstance(self.valid_from, date):
            raise ValueError("valid_from must be a date")

        for field_name in (
            "distribution_vt_czk_per_kwh",
            "distribution_nt_czk_per_kwh",
            "breaker_monthly_czk",
            "system_services_czk_per_kwh",
            "poze_czk_per_kwh",
            "non_network_monthly_czk",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{field_name} must be a finite non-negative Decimal")

        sources = tuple(self.sources)
        if not sources or any(not isinstance(item, RegulatedPriceSource) for item in sources):
            raise ValueError("sources must contain at least one RegulatedPriceSource")
        if not all(item.applies_on(self.valid_from) for item in sources):
            raise ValueError("every source must apply on the regulated tariff valid_from date")
        if not any(item.authority == RegulatedAuthority.ERU for item in sources):
            raise ValueError("regulated tariff requires at least one ERÚ source")
        object.__setattr__(self, "sources", sources)

    @property
    def variable_components(self) -> tuple[VariablePriceComponent, ...]:
        return (
            VariablePriceComponent(
                kind=PriceComponentKind.DISTRIBUTION,
                name="Regulovaná distribuce",
                high_rate_czk_per_kwh=self.distribution_vt_czk_per_kwh,
                low_rate_czk_per_kwh=self.distribution_nt_czk_per_kwh,
                includes_vat=False,
            ),
            VariablePriceComponent(
                kind=PriceComponentKind.SYSTEM_SERVICES,
                name="Systémové služby",
                high_rate_czk_per_kwh=self.system_services_czk_per_kwh,
                low_rate_czk_per_kwh=self.system_services_czk_per_kwh,
                includes_vat=False,
            ),
            VariablePriceComponent(
                kind=PriceComponentKind.POZE,
                name="POZE",
                high_rate_czk_per_kwh=self.poze_czk_per_kwh,
                low_rate_czk_per_kwh=self.poze_czk_per_kwh,
                includes_vat=False,
            ),
        )

    @property
    def fixed_components(self) -> tuple[FixedPriceComponent, ...]:
        return (
            FixedPriceComponent(
                kind=PriceComponentKind.BREAKER_FIXED,
                name="Plat za příkon podle hlavního jističe",
                monthly_czk=self.breaker_monthly_czk,
                includes_vat=False,
            ),
            FixedPriceComponent(
                kind=PriceComponentKind.OTHER_FIXED,
                name="Provoz nesíťové infrastruktury",
                monthly_czk=self.non_network_monthly_czk,
                includes_vat=False,
            ),
        )


def official_2026_baseline_sources() -> tuple[RegulatedPriceSource, ...]:
    """Return official baseline provenance for regulated household prices in 2026.

    This is deliberately named *baseline*: later ERÚ amendments (including
    1/2026) must be applied by a version-aware parser, not silently folded into
    static constants. The baseline includes the low-voltage measure, general
    regulated-price measure, the December 2025 amendment that zeroed customer
    POZE from 1 January 2026, and OTE service-price provenance.
    """
    valid_from = date(2026, 1, 1)
    return (
        RegulatedPriceSource(
            authority=RegulatedAuthority.ERU,
            document_id="Cenový výměr 14/2025 – nízké napětí",
            source_url="https://eru.gov.cz/energeticky-regulacni-vestnik-182025",
            valid_from=valid_from,
        ),
        RegulatedPriceSource(
            authority=RegulatedAuthority.ERU,
            document_id="Cenový výměr 13/2025 – ostatní regulované ceny",
            source_url="https://eru.gov.cz/energeticky-regulacni-vestnik-172025",
            valid_from=valid_from,
        ),
        RegulatedPriceSource(
            authority=RegulatedAuthority.ERU,
            document_id="Cenový výměr 15/2025 – změna pro rok 2026",
            source_url="https://eru.gov.cz/kopie-z-energeticky-regulacni-vestnik-192025",
            valid_from=valid_from,
        ),
        RegulatedPriceSource(
            authority=RegulatedAuthority.OTE,
            document_id="Ceny za služby operátora trhu v elektroenergetice 2026",
            source_url="https://www.ote-cr.cz/cs/registrace-a-smlouvy/smluvni-vztahy-elektrina/ceny-za-sluzby-ote",
            valid_from=valid_from,
        ),
    )
