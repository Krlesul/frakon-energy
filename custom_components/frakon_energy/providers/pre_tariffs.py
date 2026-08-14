"""Verified PRE household electricity price-list catalog.

PRE publishes one official PDF per product and distribution territory. The PDFs
also display regulated charges, but this supplier adapter authorizes only the
supplier-commercial extraction path. Regulated values remain governed by the
separate regulated pricing/provenance pipeline.
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

PRE_SUPPLIER = "pre"
PRE_OFFICIAL_DOMAINS = ("pre.cz",)
PRE_CURRENT_PRICES_INDEX_URL = (
    "https://www.pre.cz/cs/domacnosti/elektrina/prehled-produktu/aktualni/"
)

CONTRACT_KIND_INDEFINITE = "indefinite"
CONTRACT_KIND_FIXED = "fixed"

DISTRIBUTOR_PRE = "pre_distribuce"
DISTRIBUTOR_EGD = "eg_d"
DISTRIBUTOR_CEZ = "cez_distribuce"


@dataclass(frozen=True, slots=True)
class PreCatalogEntry:
    """One manually verified PRE household price-list reference."""

    product_name: str
    distributor: str
    source_url: str
    valid_from: date
    contract_kind: str
    aliases: tuple[str, ...] = ()


_PRE_URL_BASE = "https://www.pre.cz/cs/linky/dokumenty-ke-stazeni/cenik/elektrina"

PRE_CURRENT_COMMERCIAL_CATALOG: tuple[PreCatalogEntry, ...] = (
    PreCatalogEntry(
        product_name="PRE PROUD NEFIX",
        distributor=DISTRIBUTOR_PRE,
        source_url=f"{_PRE_URL_BASE}/pre/moo/pre-proud-nefix/",
        valid_from=date(2026, 1, 1),
        contract_kind=CONTRACT_KIND_INDEFINITE,
        aliases=("PROUD NEFIX",),
    ),
    PreCatalogEntry(
        product_name="PRE PROUD NEFIX",
        distributor=DISTRIBUTOR_EGD,
        source_url=f"{_PRE_URL_BASE}/egd/moo/pre-proud-nefix/",
        valid_from=date(2026, 1, 1),
        contract_kind=CONTRACT_KIND_INDEFINITE,
        aliases=("PROUD NEFIX",),
    ),
    PreCatalogEntry(
        product_name="PRE PROUD NEFIX",
        distributor=DISTRIBUTOR_CEZ,
        source_url=f"{_PRE_URL_BASE}/cez/moo/pre-proud-nefix/",
        valid_from=date(2026, 1, 1),
        contract_kind=CONTRACT_KIND_INDEFINITE,
        aliases=("PROUD NEFIX",),
    ),
    PreCatalogEntry(
        product_name="PRE PROUD FAVORIT 2",
        distributor=DISTRIBUTOR_PRE,
        source_url=f"{_PRE_URL_BASE}/pre/moo/pre-proud-favorit-2/",
        valid_from=date(2026, 7, 1),
        contract_kind=CONTRACT_KIND_FIXED,
        aliases=("PROUD FAVORIT 2",),
    ),
    PreCatalogEntry(
        product_name="PRE PROUD FAVORIT 2",
        distributor=DISTRIBUTOR_EGD,
        source_url=f"{_PRE_URL_BASE}/egd/moo/pre-proud-favorit-2/",
        valid_from=date(2026, 7, 1),
        contract_kind=CONTRACT_KIND_FIXED,
        aliases=("PROUD FAVORIT 2",),
    ),
    PreCatalogEntry(
        product_name="PRE PROUD FAVORIT 2",
        distributor=DISTRIBUTOR_CEZ,
        source_url=f"{_PRE_URL_BASE}/cez/moo/pre-proud-favorit-2/",
        valid_from=date(2026, 7, 1),
        contract_kind=CONTRACT_KIND_FIXED,
        aliases=("PROUD FAVORIT 2",),
    ),
    PreCatalogEntry(
        product_name="PRE PROUD FAVORIT 3",
        distributor=DISTRIBUTOR_PRE,
        source_url=f"{_PRE_URL_BASE}/pre/moo/pre-proud-favorit-3/",
        valid_from=date(2026, 7, 1),
        contract_kind=CONTRACT_KIND_FIXED,
        aliases=("PROUD FAVORIT 3",),
    ),
    PreCatalogEntry(
        product_name="PRE PROUD FAVORIT 3",
        distributor=DISTRIBUTOR_EGD,
        source_url=f"{_PRE_URL_BASE}/egd/moo/pre-proud-favorit-3/",
        valid_from=date(2026, 7, 1),
        contract_kind=CONTRACT_KIND_FIXED,
        aliases=("PROUD FAVORIT 3",),
    ),
    PreCatalogEntry(
        product_name="PRE PROUD FAVORIT 3",
        distributor=DISTRIBUTOR_CEZ,
        source_url=f"{_PRE_URL_BASE}/cez/moo/pre-proud-favorit-3/",
        valid_from=date(2026, 7, 1),
        contract_kind=CONTRACT_KIND_FIXED,
        aliases=("PROUD FAVORIT 3",),
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


def _match_entry(entry: PreCatalogEntry, product_name: str) -> tuple[int, str] | None:
    requested = _normalized_product(product_name)
    canonical = _normalized_product(entry.product_name)
    if requested == canonical:
        return (100, "exact PRE product name")
    if any(requested == _normalized_product(alias) for alias in entry.aliases):
        return (98, "exact PRE product alias")
    return None


class PreTariffCatalogAdapter:
    """Fail-closed catalog adapter for verified PRE household PDFs."""

    supplier = PRE_SUPPLIER
    official_domains = PRE_OFFICIAL_DOMAINS
    catalog_index_url = PRE_CURRENT_PRICES_INDEX_URL

    def __init__(
        self,
        *,
        catalog: tuple[PreCatalogEntry, ...] = PRE_CURRENT_COMMERCIAL_CATALOG,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._catalog = tuple(catalog)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def async_discover(
        self,
        query: TariffSourceQuery,
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
            if query.valid_on < entry.valid_from:
                continue
            if query.distributor != entry.distributor:
                continue
            if query.contract_kind != entry.contract_kind:
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
                        content_type="application/pdf",
                    ),
                    product_name=entry.product_name,
                    valid_from=entry.valid_from,
                    match_score=score,
                    match_reasons=(
                        reason,
                        "exact PRE contract kind",
                        "exact PRE distribution territory",
                        "official PRE household price-list PDF",
                        "supplier-commercial extraction only; regulated values are separate",
                    ),
                    price_scope=PRICE_SCOPE_SUPPLIER_COMMERCIAL,
                )
            )

        return tuple(
            sorted(
                candidates,
                key=lambda item: (-item.match_score, item.product_name),
            )
        )
