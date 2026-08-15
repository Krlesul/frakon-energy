"""Verified E.ON household electricity price-list catalog.

E.ON publishes household electricity price lists separately for each Czech
physical distribution territory. The verified documents below display both
supplier-commercial prices and regulated values. Supplier discovery deliberately
authorizes only the commercial extraction path: regulated values must continue
through FRAKON Energy's independent regulated pricing and provenance pipeline.

Some E.ON fixed products publish multiple display columns inside one PDF. The
three-year offer has one promotional price through 2026 and one fixed price from
2027 onward; repeated future-year columns are validation evidence for that same
fixed price, not separate customer tariff versions. Those two authoritative
periods are modeled as immutable candidates pointing at the same official PDF.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import re
from typing import Callable
import unicodedata

from ..tariff_sources import (
    PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    OfficialTariffDocument,
    TariffDocumentCandidate,
    TariffSourceQuery,
)

EON_SUPPLIER = "eon"
EON_OFFICIAL_DOMAINS = ("eon.cz",)
EON_ELECTRICITY_INDEX_URL = "https://www.eon.cz/domacnosti/elektrina/"

CONTRACT_KIND_FIXED = "fixed"

DISTRIBUTOR_EG_D = "eg_d"
DISTRIBUTOR_CEZ = "cez_distribuce"
DISTRIBUTOR_PRE = "pre_distribuce"


@dataclass(frozen=True, slots=True)
class EonCommercialPricePeriod:
    """One supplier-commercial price authority advertised inside an E.ON PDF."""

    valid_from: date
    valid_to: date | None

    def __post_init__(self) -> None:
        if not isinstance(self.valid_from, date):
            raise ValueError("valid_from must be a date")
        if self.valid_to is not None:
            if not isinstance(self.valid_to, date):
                raise ValueError("valid_to must be a date when provided")
            if self.valid_to < self.valid_from:
                raise ValueError("E.ON price period end must not precede start")

    def applies_on(self, day: date) -> bool:
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)


@dataclass(frozen=True, slots=True)
class EonCatalogEntry:
    """One immutable E.ON product/period/document mapping for one territory."""

    product_name: str
    distributor: str
    source_url: str
    valid_from: date
    valid_to: date | None = None
    document_date: date | None = None
    contract_kind: str = CONTRACT_KIND_FIXED
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("E.ON catalog validity end must not precede start")
        if self.document_date is None:
            object.__setattr__(self, "document_date", self.valid_from)


EON_PRODUCT_PERIODS: dict[str, tuple[EonCommercialPricePeriod, ...]] = {
    "Variant PRO na 2 roky": (
        EonCommercialPricePeriod(date(2026, 3, 30), None),
    ),
    "Elektřina výhodně PRO na 3 roky": (
        EonCommercialPricePeriod(date(2026, 6, 17), date(2026, 12, 31)),
        EonCommercialPricePeriod(date(2027, 1, 1), None),
    ),
}

_VARIANT_PRO_BASE = (
    "https://www.eon.cz/getmedia/PriceLists/Domacnosti/Elektrina/2026/Akvizice/"
    "ceniky_3_26/"
)
_ELEKTRINA_VYHODNE_BASE = (
    "https://www.eon.cz/getmedia/PriceLists/Domacnosti/Elektrina/2026/Akvizice/"
    "Elektrina_vyhodne_PRO_6_26/"
)

_VARIANT_URLS = {
    DISTRIBUTOR_EG_D: _VARIANT_PRO_BASE
    + "cenik--variant--pro--na--2--roky--3_26-----distribucni--uzemi--eg.d.pdf",
    DISTRIBUTOR_CEZ: _VARIANT_PRO_BASE
    + "cenik--variant--pro--na--2--roky--3_26-----distribucni--uzemi--cez.pdf",
    DISTRIBUTOR_PRE: _VARIANT_PRO_BASE
    + "cenik--variant--pro--na--2--roky--3_26-----distribucni--uzemi--pre.pdf",
}
_ELEKTRINA_VYHODNE_URLS = {
    DISTRIBUTOR_EG_D: _ELEKTRINA_VYHODNE_BASE
    + "cenik--elektrina--vyhodne--pro--na--3--roky--6_26-----distribucni--uzemi--eg.d.pdf",
    DISTRIBUTOR_CEZ: _ELEKTRINA_VYHODNE_BASE
    + "cenik--elektrina--vyhodne--pro--na--3--roky--6_26-----distribucni--uzemi--cez.pdf",
    DISTRIBUTOR_PRE: _ELEKTRINA_VYHODNE_BASE
    + "cenik--elektrina--vyhodne--pro--na--3--roky--6_26-----distribucni--uzemi--pre.pdf",
}


def _catalog_entries() -> tuple[EonCatalogEntry, ...]:
    entries: list[EonCatalogEntry] = []
    variant_period = EON_PRODUCT_PERIODS["Variant PRO na 2 roky"][0]
    for distributor, source_url in _VARIANT_URLS.items():
        entries.append(
            EonCatalogEntry(
                product_name="Variant PRO na 2 roky",
                distributor=distributor,
                source_url=source_url,
                valid_from=variant_period.valid_from,
                valid_to=variant_period.valid_to,
                document_date=date(2026, 3, 30),
            )
        )

    for distributor, source_url in _ELEKTRINA_VYHODNE_URLS.items():
        for period in EON_PRODUCT_PERIODS["Elektřina výhodně PRO na 3 roky"]:
            entries.append(
                EonCatalogEntry(
                    product_name="Elektřina výhodně PRO na 3 roky",
                    distributor=distributor,
                    source_url=source_url,
                    valid_from=period.valid_from,
                    valid_to=period.valid_to,
                    document_date=date(2026, 6, 17),
                )
            )
    return tuple(entries)


EON_2026_ELECTRICITY_CATALOG: tuple[EonCatalogEntry, ...] = _catalog_entries()

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalized_product(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return " ".join(part for part in _NON_ALNUM_RE.split(ascii_text) if part)


def _match_entry(entry: EonCatalogEntry, product_name: str) -> tuple[int, str] | None:
    requested = _normalized_product(product_name)
    canonical = _normalized_product(entry.product_name)
    if requested == canonical:
        return (100, "exact E.ON product name")
    if any(requested == _normalized_product(alias) for alias in entry.aliases):
        return (98, "exact verified E.ON product alias")
    return None


def eon_contract_product_matches_candidate(
    *,
    candidate_product_name: str,
    contract_product_name: str,
    contract_kind: str,
    catalog: tuple[EonCatalogEntry, ...] = EON_2026_ELECTRICITY_CATALOG,
) -> bool:
    """Verify contract identity against the same exact E.ON catalog rules.

    The catalog contains duplicate canonical product names because territories
    and immutable price periods are separate entries. They are acceptable only
    when all matching entries agree on the exact product/alias contract rule.
    No fuzzy, substring or cross-product matching is performed here.
    """
    canonical = _normalized_product(candidate_product_name)
    if not canonical or not isinstance(contract_kind, str) or not contract_kind.strip():
        return False
    entries = tuple(
        entry
        for entry in catalog
        if _normalized_product(entry.product_name) == canonical
        and entry.contract_kind == contract_kind.strip()
    )
    if not entries:
        return False
    decisions = {_match_entry(entry, contract_product_name) is not None for entry in entries}
    return decisions == {True}


class EonTariffCatalogAdapter:
    """Fail-closed adapter for currently verified E.ON household price lists."""

    supplier = EON_SUPPLIER
    official_domains = EON_OFFICIAL_DOMAINS
    catalog_index_url = EON_ELECTRICITY_INDEX_URL

    def __init__(
        self,
        *,
        catalog: tuple[EonCatalogEntry, ...] = EON_2026_ELECTRICITY_CATALOG,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._catalog = tuple(catalog)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def async_discover(
        self, query: TariffSourceQuery
    ) -> tuple[TariffDocumentCandidate, ...]:
        if not isinstance(query, TariffSourceQuery):
            raise ValueError("query must be TariffSourceQuery")
        if query.supplier != self.supplier:
            return ()

        discovered_at = self._clock()
        if not isinstance(discovered_at, datetime) or discovered_at.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")

        candidates: list[TariffDocumentCandidate] = []
        for entry in self._catalog:
            if query.contract_kind != entry.contract_kind:
                continue
            if query.distributor != entry.distributor:
                continue
            if query.valid_on < entry.valid_from:
                continue
            if entry.valid_to is not None and query.valid_on > entry.valid_to:
                continue
            match = _match_entry(entry, query.product_name)
            if match is None:
                continue
            score, reason = match
            candidates.append(
                TariffDocumentCandidate(
                    document=OfficialTariffDocument(
                        supplier=self.supplier,
                        source_url=entry.source_url,
                        discovered_at=discovered_at,
                        document_date=entry.document_date,
                        content_type="application/pdf",
                    ),
                    product_name=entry.product_name,
                    valid_from=entry.valid_from,
                    valid_to=entry.valid_to,
                    match_score=score,
                    match_reasons=(
                        reason,
                        "exact fixed-contract kind from official E.ON price list",
                        "exact Czech distribution territory from official E.ON price list",
                        "exact supplier-commercial price period from official E.ON price list",
                        "official E.ON household electricity PDF on eon.cz",
                        "supplier-commercial extraction only; regulated values are separate",
                    ),
                    price_scope=PRICE_SCOPE_SUPPLIER_COMMERCIAL,
                )
            )

        return tuple(
            sorted(
                candidates,
                key=lambda item: (
                    -item.match_score,
                    item.product_name,
                    item.valid_from,
                    item.document.source_url,
                ),
            )
        )
