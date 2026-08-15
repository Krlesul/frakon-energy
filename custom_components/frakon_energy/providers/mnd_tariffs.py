"""Fail-closed MND household electricity tariff discovery boundary.

MND's official household price-list page resolves supply documents dynamically
from the customer's postcode. The public HTML therefore does not provide a
stable supply-PDF URL that can be safely hard-coded from a product name alone.

This adapter deliberately separates two authorities:

* immutable, manually verified MND product identity and contract kind; and
* an injected resolver that must return the exact official MND document selected
  for the already-normalized customer query.

Without a resolver, or without an explicit normalized postcode in the operational
source context, the adapter returns no candidate and does not invoke the resolver.
A resolver result is accepted only when product, contract kind, distribution
territory, validity and an official ``/documents/view/<uuid>`` HTTPS URL all
agree. The postcode is never copied into the candidate or price provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Protocol, runtime_checkable
import unicodedata
from urllib.parse import urlparse
from uuid import UUID

from ..tariff_sources import (
    PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    OfficialTariffDocument,
    TariffDocumentCandidate,
    TariffSourceQuery,
)

MND_SUPPLIER = "mnd"
MND_OFFICIAL_DOMAINS = ("mnd.cz",)
MND_ELECTRICITY_INDEX_URL = "https://prod.mnd.cz/elektrina-domacnosti"

CONTRACT_KIND_FIXED = "fixed"
CONTRACT_KIND_INDEFINITE = "indefinite"


@dataclass(frozen=True, slots=True)
class MndProductDefinition:
    """One current MND product identity that may be resolved to an official PDF."""

    product_name: str
    contract_kind: str

    def __post_init__(self) -> None:
        if not isinstance(self.product_name, str) or not self.product_name.strip():
            raise ValueError("product_name must not be empty")
        if self.contract_kind not in (CONTRACT_KIND_FIXED, CONTRACT_KIND_INDEFINITE):
            raise ValueError("unsupported MND contract kind")


MND_CURRENT_ELECTRICITY_PRODUCTS: tuple[MndProductDefinition, ...] = (
    MndProductDefinition(product_name="Proud - Ceník Říjen 28", contract_kind=CONTRACT_KIND_FIXED),
    MndProductDefinition(product_name="Proud - Klesající ceník Duben 29", contract_kind=CONTRACT_KIND_FIXED),
    MndProductDefinition(product_name="Proud - Domácnosti", contract_kind=CONTRACT_KIND_INDEFINITE),
)


@dataclass(frozen=True, slots=True)
class MndResolvedTariffSource:
    """Exact document result from a postcode-/territory-aware MND resolver."""

    product_name: str
    distributor: str
    contract_kind: str
    source_url: str
    valid_from: date
    discovered_at: datetime
    valid_to: date | None = None
    document_date: date | None = None

    def __post_init__(self) -> None:
        for field_name in ("product_name", "distributor", "contract_kind", "source_url"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")
        if not isinstance(self.valid_from, date):
            raise ValueError("valid_from must be a date")
        if self.valid_to is not None:
            if not isinstance(self.valid_to, date):
                raise ValueError("valid_to must be a date")
            if self.valid_to < self.valid_from:
                raise ValueError("valid_to must not precede valid_from")
        if not isinstance(self.discovered_at, datetime) or self.discovered_at.tzinfo is None:
            raise ValueError("discovered_at must be a timezone-aware datetime")
        if self.document_date is not None and not isinstance(self.document_date, date):
            raise ValueError("document_date must be a date")
        _validate_mnd_document_url(self.source_url)


@runtime_checkable
class MndTariffSourceResolver(Protocol):
    """Resolve one exact MND product/query pair to its official PDF source."""

    async def async_resolve(
        self,
        query: TariffSourceQuery,
        product: MndProductDefinition,
    ) -> MndResolvedTariffSource | None:
        """Return the exact official source, or None when it cannot be proven."""
        ...


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalized_product(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(part for part in _NON_ALNUM_RE.split(ascii_text) if part)


def _is_mnd_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    return host == "mnd.cz" or host.endswith(".mnd.cz")


def _validate_mnd_document_url(source_url: str) -> None:
    parsed = urlparse(source_url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("MND source URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("MND source URL must not contain embedded credentials")
    try:
        port = parsed.port
    except ValueError as err:
        raise ValueError("MND source URL contains an invalid port") from err
    if port not in (None, 443):
        raise ValueError("MND source URL must use the standard HTTPS port")
    if not _is_mnd_host(parsed.hostname):
        raise ValueError("MND source URL must use an official mnd.cz host")
    if parsed.query or parsed.fragment or parsed.params:
        raise ValueError("MND source URL must not contain parameters, query or fragment")

    prefix = "/documents/view/"
    if not parsed.path.startswith(prefix):
        raise ValueError("MND source URL must use /documents/view/<uuid>")
    identifier = parsed.path[len(prefix) :]
    if not identifier or "/" in identifier:
        raise ValueError("MND source URL must contain exactly one document UUID")
    try:
        parsed_uuid = UUID(identifier)
    except (ValueError, AttributeError) as err:
        raise ValueError("MND source URL contains an invalid document UUID") from err
    if str(parsed_uuid) != identifier.lower():
        raise ValueError("MND source URL must contain a canonical document UUID")


def _matching_product(
    products: tuple[MndProductDefinition, ...],
    query: TariffSourceQuery,
) -> MndProductDefinition | None:
    requested = _normalized_product(query.product_name)
    matches = tuple(
        product
        for product in products
        if _normalized_product(product.product_name) == requested
        and product.contract_kind == query.contract_kind
    )
    if len(matches) > 1:
        raise ValueError("duplicate MND product identity in catalog")
    return matches[0] if matches else None


class MndTariffCatalogAdapter:
    """Fail-closed MND adapter around an exact official-document resolver."""

    supplier = MND_SUPPLIER
    official_domains = MND_OFFICIAL_DOMAINS
    catalog_index_url = MND_ELECTRICITY_INDEX_URL

    def __init__(
        self,
        *,
        resolver: MndTariffSourceResolver | None = None,
        products: tuple[MndProductDefinition, ...] = MND_CURRENT_ELECTRICITY_PRODUCTS,
    ) -> None:
        if resolver is not None and not isinstance(resolver, MndTariffSourceResolver):
            raise ValueError("resolver must implement MndTariffSourceResolver")
        self._resolver = resolver
        self._products = tuple(products)
        normalized_keys: set[tuple[str, str]] = set()
        for product in self._products:
            if not isinstance(product, MndProductDefinition):
                raise ValueError("products must contain MndProductDefinition items")
            key = (_normalized_product(product.product_name), product.contract_kind)
            if key in normalized_keys:
                raise ValueError("duplicate MND product identity in catalog")
            normalized_keys.add(key)

    async def async_discover(self, query: TariffSourceQuery) -> tuple[TariffDocumentCandidate, ...]:
        if not isinstance(query, TariffSourceQuery):
            raise ValueError("query must be TariffSourceQuery")
        if query.supplier != self.supplier:
            return ()

        product = _matching_product(self._products, query)
        if product is None or self._resolver is None:
            return ()
        if query.source_context.postcode is None:
            # Do not invoke a dynamic MND resolver without the customer-supplied
            # lookup key that MND itself requires. No postcode inference is allowed.
            return ()

        resolved = await self._resolver.async_resolve(query, product)
        if resolved is None:
            return ()
        if not isinstance(resolved, MndResolvedTariffSource):
            raise ValueError("MND resolver returned an invalid source")
        if _normalized_product(resolved.product_name) != _normalized_product(product.product_name):
            raise ValueError("MND resolver product does not match verified product")
        if resolved.contract_kind != product.contract_kind:
            raise ValueError("MND resolver contract kind does not match query")
        if resolved.distributor != query.distributor:
            raise ValueError("MND resolver distribution territory does not match query")
        if query.valid_on < resolved.valid_from or (
            resolved.valid_to is not None and query.valid_on > resolved.valid_to
        ):
            return ()

        return (
            TariffDocumentCandidate(
                document=OfficialTariffDocument(
                    supplier=self.supplier,
                    source_url=resolved.source_url,
                    discovered_at=resolved.discovered_at,
                    document_date=resolved.document_date,
                    content_type="application/pdf",
                ),
                product_name=product.product_name,
                valid_from=resolved.valid_from,
                valid_to=resolved.valid_to,
                match_score=100,
                match_reasons=(
                    "exact MND product name",
                    "exact MND contract kind",
                    "exact MND distribution territory from resolver",
                    "official MND /documents/view document resolved for customer context",
                    "supplier-commercial extraction only; regulated values are separate",
                ),
                price_scope=PRICE_SCOPE_SUPPLIER_COMMERCIAL,
            ),
        )
