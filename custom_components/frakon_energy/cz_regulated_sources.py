"""Official Czech regulated electricity source/input adapter.

The module anchors universal 2026 values to ERÚ/OTE sources, but deliberately
requires tariff-, breaker- and amendment-dependent values to be supplied by a
version-aware parser. It converts those verified inputs into the single
`RegulatedTariffBundle` model used by FRAKON Energy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
import re
from urllib.parse import urlsplit

from .pricing import FixedPriceComponent, PriceComponentKind, VariablePriceComponent
from .regulated_pricing import (
    NON_NETWORK_INFRASTRUCTURE_COMPONENT_NAME,
    RegulatedTariffBundle,
)
from .tariff_provenance import PriceEvidence
from .tariff_sources import PRICE_SCOPE_REGULATED

_DISTRIBUTION_TARIFF_RE = re.compile(r"^D\d{2}d$")
_BREAKER_RE = re.compile(r"^(?:1|3)x[1-9]\d*A$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Confirmed by official 2026 ERÚ/OTE publications. These are the only monetary
# values frozen here. Table-/amendment-dependent values remain explicit parser
# inputs and must never be guessed.
POZE_2026_CZK_PER_KWH = Decimal("0")
OTE_NON_NETWORK_2026_CZK_PER_MONTH = Decimal("12.87")

# A later ERÚ amendment to price measure 13/2025 is kept explicit rather than
# silently folded into the January baseline. A version-aware parser must decide
# applicability and values for the requested date.
ERU_1_2026_AMENDMENT_PAGE = "https://eru.gov.cz/energeticky-regulacni-vestnik-22026"


class RegulatedAuthority(StrEnum):
    ERU = "eru"
    OTE = "ote"
    CUSTOMS = "celni_sprava"


_OFFICIAL_DOMAINS = {
    RegulatedAuthority.ERU: "eru.gov.cz",
    RegulatedAuthority.OTE: "ote-cr.cz",
    RegulatedAuthority.CUSTOMS: "celnisprava.gov.cz",
}
_SOURCE_NAMES = {
    RegulatedAuthority.ERU: "Energetický regulační úřad",
    RegulatedAuthority.OTE: "OTE",
    RegulatedAuthority.CUSTOMS: "Celní správa ČR",
}


def _finite_nonnegative(value: Decimal, field_name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{field_name} must be a finite non-negative Decimal")
    return value


def _normalize_tariff(value: str) -> str:
    tariff = value.strip() if isinstance(value, str) else ""
    tariff = tariff[0].upper() + tariff[1:-1] + tariff[-1].lower() if tariff else ""
    if not _DISTRIBUTION_TARIFF_RE.fullmatch(tariff):
        raise ValueError("distribution_tariff must use a code such as D25d")
    return tariff


@dataclass(frozen=True, slots=True)
class RegulatedPriceSource:
    """One official authority source used by a parsed regulated value set."""

    authority: RegulatedAuthority
    document_id: str
    source_url: str
    valid_from: date
    valid_to: date | None = None
    document_date: date | None = None
    checksum: str | None = None

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
        if self.document_date is not None and not isinstance(self.document_date, date):
            raise ValueError("document_date must be a date")
        if self.checksum is not None:
            checksum = self.checksum.lower() if isinstance(self.checksum, str) else ""
            if not _SHA256_RE.fullmatch(checksum):
                raise ValueError("checksum must be a lowercase SHA-256 digest")
            object.__setattr__(self, "checksum", checksum)

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
        if not isinstance(day, date):
            raise ValueError("day must be a date")
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)

    def as_price_evidence(self) -> PriceEvidence:
        return PriceEvidence(
            scope=PRICE_SCOPE_REGULATED,
            source_name=_SOURCE_NAMES[self.authority],
            document_name=self.document_id,
            source_url=self.source_url,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
            document_date=self.document_date,
            checksum=self.checksum,
        )


@dataclass(frozen=True, slots=True)
class CzechRegulatedTariffInputs:
    """Fully parsed Czech regulated inputs before canonical conversion.

    All monetary values use the official publication convention (CZK excluding
    VAT). The caller must explicitly supply every table-/amendment-dependent
    value, including electricity tax; only the source-anchored universal POZE and
    OTE values have safe 2026 defaults.
    """

    distributor: str
    distribution_tariff: str
    breaker_code: str
    valid_from: date
    distribution_vt_czk_per_kwh: Decimal
    distribution_nt_czk_per_kwh: Decimal
    breaker_monthly_czk: Decimal
    system_services_czk_per_kwh: Decimal
    electricity_tax_czk_per_kwh: Decimal
    sources: tuple[RegulatedPriceSource, ...]
    poze_czk_per_kwh: Decimal = POZE_2026_CZK_PER_KWH
    non_network_monthly_czk: Decimal = OTE_NON_NETWORK_2026_CZK_PER_MONTH
    valid_to: date | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.distributor, str) or not self.distributor.strip():
            raise ValueError("distributor must not be empty")
        object.__setattr__(self, "distribution_tariff", _normalize_tariff(self.distribution_tariff))
        if not isinstance(self.breaker_code, str) or not _BREAKER_RE.fullmatch(self.breaker_code):
            raise ValueError("breaker_code must use a code such as 3x25A")
        if not isinstance(self.valid_from, date):
            raise ValueError("valid_from must be a date")
        if self.valid_to is not None:
            if not isinstance(self.valid_to, date):
                raise ValueError("valid_to must be a date")
            if self.valid_to < self.valid_from:
                raise ValueError("regulated validity end must not precede start")

        for field_name in (
            "distribution_vt_czk_per_kwh",
            "distribution_nt_czk_per_kwh",
            "breaker_monthly_czk",
            "system_services_czk_per_kwh",
            "electricity_tax_czk_per_kwh",
            "poze_czk_per_kwh",
            "non_network_monthly_czk",
        ):
            _finite_nonnegative(getattr(self, field_name), field_name)

        sources = tuple(self.sources)
        if not sources or any(not isinstance(item, RegulatedPriceSource) for item in sources):
            raise ValueError("sources must contain at least one RegulatedPriceSource")
        if not any(item.authority == RegulatedAuthority.ERU for item in sources):
            raise ValueError("regulated inputs require at least one ERÚ source")
        if not all(item.applies_on(self.valid_from) for item in sources):
            raise ValueError("every source must apply on valid_from")
        if self.valid_to is not None and not all(item.applies_on(self.valid_to) for item in sources):
            raise ValueError("every source must cover valid_to")
        object.__setattr__(self, "sources", sources)

    def regulated_evidence(self) -> tuple[PriceEvidence, ...]:
        return tuple(item.as_price_evidence() for item in self.sources)

    def to_bundle(self, *, confirmed: bool = False) -> RegulatedTariffBundle:
        """Convert verified Czech inputs into the canonical regulated bundle."""

        primary = next(
            item for item in self.sources if item.authority == RegulatedAuthority.ERU
        )
        return RegulatedTariffBundle(
            distributor=self.distributor,
            distribution_tariff=self.distribution_tariff,
            breaker_code=self.breaker_code,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
            variable_components=(
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
                VariablePriceComponent(
                    kind=PriceComponentKind.ELECTRICITY_TAX,
                    name="Daň z elektřiny",
                    high_rate_czk_per_kwh=self.electricity_tax_czk_per_kwh,
                    low_rate_czk_per_kwh=self.electricity_tax_czk_per_kwh,
                    includes_vat=False,
                ),
            ),
            fixed_components=(
                FixedPriceComponent(
                    kind=PriceComponentKind.BREAKER_FIXED,
                    name="Plat za příkon podle hlavního jističe",
                    monthly_czk=self.breaker_monthly_czk,
                    includes_vat=False,
                ),
                FixedPriceComponent(
                    kind=PriceComponentKind.OTHER_FIXED,
                    name=NON_NETWORK_INFRASTRUCTURE_COMPONENT_NAME,
                    monthly_czk=self.non_network_monthly_czk,
                    includes_vat=False,
                ),
            ),
            source_url=primary.source_url,
            document_date=primary.document_date,
            checksum=primary.checksum,
            confirmed=confirmed,
        )


def official_2026_baseline_sources() -> tuple[RegulatedPriceSource, ...]:
    """Return January-2026 baseline source metadata, not a current tariff result.

    ERÚ later published price measure 1/2026 amending 13/2025. Callers must use a
    version-aware parser before treating these sources as sufficient for a later
    date; this function is only the source catalog for the 1 January baseline.
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
