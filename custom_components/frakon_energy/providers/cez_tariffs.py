"""Verified ČEZ household commercial price-list catalog.

The entries in this module point to official ČEZ Prodej PDF documents. They
contain only the supplier's commercial (unregulated) energy price and supplier
standing charge. They are deliberately classified as supplier-commercial
sources and must be combined with regulated distribution components before an
all-in tariff can be produced.
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

CEZ_SUPPLIER = "cez"
CEZ_OFFICIAL_DOMAINS = ("cez.cz",)
CEZ_CURRENT_PRICES_INDEX_URL = "https://www.cez.cz/cs/nove-ceny"
CEZ_2026_COMMERCIAL_VALID_FROM = date(2026, 1, 1)

CONTRACT_KIND_INDEFINITE = "indefinite"
CONTRACT_KIND_FIXED = "fixed"


@dataclass(frozen=True, slots=True)
class CezCatalogEntry:
    """One immutable, manually verified ČEZ commercial price-list reference."""

    product_name: str
    source_url: str
    valid_from: date
    contract_kind: str
    aliases: tuple[str, ...] = ()


CEZ_2026_COMMERCIAL_CATALOG: tuple[CezCatalogEntry, ...] = (
    CezCatalogEntry(
        product_name="Elektřina na dobu neurčitou",
        source_url="https://www.cez.cz/file/edee/2025/10/x01_moo_ee_na_dobu_neurcitou.pdf",
        valid_from=CEZ_2026_COMMERCIAL_VALID_FROM,
        contract_kind=CONTRACT_KIND_INDEFINITE,
        aliases=("Na dobu neurčitou",),
    ),
    CezCatalogEntry(
        product_name="Basic",
        source_url="https://www.cez.cz/file/edee/2025/10/x02_moo_ee_basic.pdf",
        valid_from=CEZ_2026_COMMERCIAL_VALID_FROM,
        contract_kind=CONTRACT_KIND_INDEFINITE,
        aliases=("Elektřina Basic",),
    ),
    CezCatalogEntry(
        product_name="eTarif",
        source_url="https://www.cez.cz/file/edee/2025/10/x03_moo_ee_etarif.pdf",
        valid_from=CEZ_2026_COMMERCIAL_VALID_FROM,
        contract_kind=CONTRACT_KIND_INDEFINITE,
        aliases=("Elektřina eTarif",),
    ),
    CezCatalogEntry(
        product_name="Zelená elektřina",
        source_url="https://www.cez.cz/file/edee/2025/10/x05_moo_ee_zelena_elektrina.pdf",
        valid_from=CEZ_2026_COMMERCIAL_VALID_FROM,
        contract_kind=CONTRACT_KIND_INDEFINITE,
    ),
    CezCatalogEntry(
        product_name="Elektřina pro ZTP",
        source_url="https://www.cez.cz/file/edee/2025/10/x06_moo_ee_pro_ztp.pdf",
        valid_from=CEZ_2026_COMMERCIAL_VALID_FROM,
        contract_kind=CONTRACT_KIND_INDEFINITE,
    ),
    CezCatalogEntry(
        product_name="Krátko odběr",
        source_url="https://www.cez.cz/file/edee/2025/10/x07_moo_ee_kratko-odber.pdf",
        valid_from=CEZ_2026_COMMERCIAL_VALID_FROM,
        contract_kind=CONTRACT_KIND_FIXED,
        aliases=("Elektřina Krátko odběr",),
    ),
    CezCatalogEntry(
        product_name="Elektřina bez závazku",
        source_url="https://www.cez.cz/file/edee/2025/10/x08_moo_ee_bez-zavazku.pdf",
        valid_from=CEZ_2026_COMMERCIAL_VALID_FROM,
        contract_kind=CONTRACT_KIND_INDEFINITE,
    ),
)

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalized_product(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(part for part in _NON_ALNUM_RE.split(ascii_text) if part)


def _match_entry(entry: CezCatalogEntry, product_name: str) -> tuple[int, str] | None:
    requested = _normalized_product(product_name)
    canonical = _normalized_product(entry.product_name)
    if requested == canonical:
        return (100, "exact ČEZ product name")
    if any(requested == _normalized_product(alias) for alias in entry.aliases):
        return (98, "exact official ČEZ product alias")
    return None


class CezTariffCatalogAdapter:
    """Fail-closed catalog adapter for currently verified ČEZ household PDFs."""

    supplier = CEZ_SUPPLIER
    official_domains = CEZ_OFFICIAL_DOMAINS
    catalog_index_url = CEZ_CURRENT_PRICES_INDEX_URL

    def __init__(
        self,
        *,
        catalog: tuple[CezCatalogEntry, ...] = CEZ_2026_COMMERCIAL_CATALOG,
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
            if query.valid_on < entry.valid_from:
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
                        "exact contract kind from official ČEZ price list",
                        "official ČEZ household price-list PDF",
                        "supplier commercial price only; regulated components are separate",
                    ),
                    price_scope=PRICE_SCOPE_SUPPLIER_COMMERCIAL,
                )
            )

        return tuple(sorted(candidates, key=lambda item: (-item.match_score, item.product_name)))