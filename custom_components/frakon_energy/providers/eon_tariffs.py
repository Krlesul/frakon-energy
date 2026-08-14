"""Verified E.ON household electricity price-list catalog.

E.ON publishes household electricity price lists separately for each Czech
physical distribution territory. The verified documents below display both
supplier-commercial prices and regulated values. Supplier discovery deliberately
authorizes only the commercial extraction path: regulated values must continue
through FRAKON Energy's independent regulated pricing and provenance pipeline.

Discovery is intentionally fail-closed: supplier, product name, fixed-contract
kind, distribution territory and commercial-price validity start must all match
an immutable verified catalog entry. No fuzzy product inference is performed.
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
class EonCatalogEntry:
    """One immutable E.ON product/document mapping for a distribution territory."""

    product_name: str
    distributor: str
    source_url: str
    valid_from: date
    contract_kind: str = CONTRACT_KIND_FIXED
    aliases: tuple[str, ...] = ()


_VARIANT_PRO_BASE = (
    "https://www.eon.cz/getmedia/PriceLists/Domacnosti/Elektrina/2026/Akvizice/"
    "ceniky_3_26/"
)
_ELEKTRINA_VYHODNE_BASE = (
    "https://www.eon.cz/getmedia/PriceLists/Domacnosti/Elektrina/2026/Akvizice/"
    "Elektrina_vyhodne_PRO_6_26/"
)

EON_2026_ELECTRICITY_CATALOG: tuple[EonCatalogEntry, ...] = (
    EonCatalogEntry(
        product_name="Variant PRO na 2 roky",
        distributor=DISTRIBUTOR_EG_D,
        source_url=(
            _VARIANT_PRO_BASE
            + "cenik--variant--pro--na--2--roky--3_26-----distribucni--uzemi--eg.d.pdf"
        ),
        valid_from=date(2026, 3, 30),
    ),
    EonCatalogEntry(
        product_name="Variant PRO na 2 roky",
        distributor=DISTRIBUTOR_CEZ,
        source_url=(
            _VARIANT_PRO_BASE
            + "cenik--variant--pro--na--2--roky--3_26-----distribucni--uzemi--cez.pdf"
        ),
        valid_from=date(2026, 3, 30),
    ),
    EonCatalogEntry(
        product_name="Variant PRO na 2 roky",
        distributor=DISTRIBUTOR_PRE,
        source_url=(
            _VARIANT_PRO_BASE
            + "cenik--variant--pro--na--2--roky--3_26-----distribucni--uzemi--pre.pdf"
        ),
        valid_from=date(2026, 3, 30),
    ),
    EonCatalogEntry(
        product_name="Elektřina výhodně PRO na 3 roky",
        distributor=DISTRIBUTOR_EG_D,
        source_url=(
            _ELEKTRINA_VYHODNE_BASE
            + "cenik--elektrina--vyhodne--pro--na--3--roky--6_26-----distribucni--uzemi--eg.d.pdf"
        ),
        valid_from=date(2026, 6, 17),
    ),
    EonCatalogEntry(
        product_name="Elektřina výhodně PRO na 3 roky",
        distributor=DISTRIBUTOR_CEZ,
        source_url=(
            _ELEKTRINA_VYHODNE_BASE
            + "cenik--elektrina--vyhodne--pro--na--3--roky--6_26-----distribucni--uzemi--cez.pdf"
        ),
        valid_from=date(2026, 6, 17),
    ),
    EonCatalogEntry(
        product_name="Elektřina výhodně PRO na 3 roky",
        distributor=DISTRIBUTOR_PRE,
        source_url=(
            _ELEKTRINA_VYHODNE_BASE
            + "cenik--elektrina--vyhodne--pro--na--3--roky--6_26-----distribucni--uzemi--pre.pdf"
        ),
        valid_from=date(2026, 6, 17),
    ),
)

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
                        document_date=entry.valid_from,
                        content_type="application/pdf",
                    ),
                    product_name=entry.product_name,
                    valid_from=entry.valid_from,
                    match_score=score,
                    match_reasons=(
                        reason,
                        "exact fixed-contract kind from official E.ON price list",
                        "exact Czech distribution territory from official E.ON price list",
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
                    item.document.source_url,
                ),
            )
        )
