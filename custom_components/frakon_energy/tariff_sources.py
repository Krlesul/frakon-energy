"""Safe supplier price-list discovery contracts for FRAKON Energy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable
from urllib.parse import urlparse

PRICE_SCOPE_UNKNOWN = "unknown"
PRICE_SCOPE_SUPPLIER_COMMERCIAL = "supplier_commercial"
PRICE_SCOPE_REGULATED = "regulated"
PRICE_SCOPE_ALL_IN = "all_in"
PRICE_SCOPES = (
    PRICE_SCOPE_UNKNOWN,
    PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    PRICE_SCOPE_REGULATED,
    PRICE_SCOPE_ALL_IN,
)

_SUPPLIER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_DISTRIBUTION_TARIFF_RE = re.compile(r"^D\d{2}d$")
_BREAKER_RE = re.compile(r"^(?:1|3)x[1-9]\d*A$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
_CZECH_POSTCODE_RE = re.compile(r"^[1-7]\d{4}$")


def normalize_czech_postcode(value: str) -> str:
    """Normalize a Czech PSČ to five digits without inferring location."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("postcode must not be empty")
    normalized = re.sub(r"\s+", "", value)
    if not _CZECH_POSTCODE_RE.fullmatch(normalized):
        raise ValueError("postcode must be a valid five-digit Czech PSČ")
    return normalized


@dataclass(frozen=True, slots=True)
class TariffSourceResolutionContext:
    """Operational lookup context that is never tariff price authority."""

    postcode: str | None = None

    def __post_init__(self) -> None:
        if self.postcode is not None:
            object.__setattr__(self, "postcode", normalize_czech_postcode(self.postcode))

    @property
    def is_empty(self) -> bool:
        return self.postcode is None

    def as_dict(self) -> dict[str, str]:
        return {} if self.postcode is None else {"postcode": self.postcode}

    @classmethod
    def from_value(cls, value: Mapping[str, Any] | None) -> TariffSourceResolutionContext:
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise ValueError("source_context must be an object")
        unexpected = set(value) - {"postcode"}
        if unexpected:
            raise ValueError(
                "source_context contains unsupported fields: "
                + ", ".join(sorted(str(item) for item in unexpected))
            )
        postcode = value.get("postcode")
        if postcode in (None, ""):
            return cls()
        return cls(postcode=postcode)


def tariff_source_context_fingerprint(context: TariffSourceResolutionContext) -> str:
    """Return a stable operational fingerprint, never a price fingerprint."""
    if not isinstance(context, TariffSourceResolutionContext):
        raise ValueError("context must be TariffSourceResolutionContext")
    encoded = json.dumps(
        context.as_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _non_empty(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value.strip()


def _supplier_slug(value: str) -> str:
    supplier = _non_empty(value, "supplier").lower()
    if not _SUPPLIER_RE.fullmatch(supplier):
        raise ValueError("supplier must be a lowercase slug")
    return supplier


def _official_domain(value: str) -> str:
    domain = _non_empty(value, "official domain").lower().rstrip(".")
    if not _DOMAIN_RE.fullmatch(domain):
        raise ValueError(f"invalid official domain: {value}")
    return domain


def _url_host(value: str) -> str:
    url = _non_empty(value, "source_url")
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("source_url must use HTTPS and contain a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("source_url must not contain embedded credentials")
    try:
        port = parsed.port
    except ValueError as err:
        raise ValueError("source_url contains an invalid port") from err
    if port not in (None, 443):
        raise ValueError("source_url must not use a nonstandard HTTPS port")
    return parsed.hostname.lower().rstrip(".")


def _host_matches_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


@dataclass(frozen=True, slots=True)
class TariffSourceQuery:
    """Normalized contract fields plus non-price context for official discovery."""

    supplier: str
    product_name: str
    distributor: str
    contract_kind: str
    distribution_tariff: str
    breaker_code: str
    valid_on: date
    source_context: TariffSourceResolutionContext = TariffSourceResolutionContext()

    def __post_init__(self) -> None:
        object.__setattr__(self, "supplier", _supplier_slug(self.supplier))
        object.__setattr__(self, "product_name", _non_empty(self.product_name, "product_name"))
        object.__setattr__(self, "distributor", _non_empty(self.distributor, "distributor"))
        object.__setattr__(self, "contract_kind", _non_empty(self.contract_kind, "contract_kind"))
        tariff = _non_empty(self.distribution_tariff, "distribution_tariff")
        tariff = tariff[0].upper() + tariff[1:-1] + tariff[-1].lower()
        if not _DISTRIBUTION_TARIFF_RE.fullmatch(tariff):
            raise ValueError("distribution_tariff must use a code such as D25d")
        object.__setattr__(self, "distribution_tariff", tariff)
        breaker = _non_empty(self.breaker_code, "breaker_code")
        if not _BREAKER_RE.fullmatch(breaker):
            raise ValueError("breaker_code must use a code such as 3x25A")
        object.__setattr__(self, "breaker_code", breaker)
        if not isinstance(self.valid_on, date):
            raise ValueError("valid_on must be a date")
        if not isinstance(self.source_context, TariffSourceResolutionContext):
            raise ValueError("source_context must be TariffSourceResolutionContext")


@dataclass(frozen=True, slots=True)
class OfficialTariffDocument:
    """Metadata for one document retrieved only from an official HTTPS source."""

    supplier: str
    source_url: str
    discovered_at: datetime
    document_date: date | None = None
    sha256: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    content_type: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "supplier", _supplier_slug(self.supplier))
        _url_host(self.source_url)
        if not isinstance(self.discovered_at, datetime) or self.discovered_at.tzinfo is None:
            raise ValueError("discovered_at must be a timezone-aware datetime")
        if self.document_date is not None and not isinstance(self.document_date, date):
            raise ValueError("document_date must be a date")
        if self.sha256 is not None:
            checksum = _non_empty(self.sha256, "sha256").lower()
            if not _SHA256_RE.fullmatch(checksum):
                raise ValueError("sha256 must be a 64-character lowercase hex digest")
            object.__setattr__(self, "sha256", checksum)
        for field_name in ("etag", "last_modified", "content_type"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field_name} must be non-empty when provided")


@dataclass(frozen=True, slots=True)
class TariffDocumentCandidate:
    """A supplier adapter suggestion that still requires customer confirmation."""

    document: OfficialTariffDocument
    product_name: str
    valid_from: date
    valid_to: date | None = None
    match_score: int = 0
    match_reasons: tuple[str, ...] = ()
    price_scope: str = PRICE_SCOPE_UNKNOWN

    def __post_init__(self) -> None:
        if not isinstance(self.document, OfficialTariffDocument):
            raise ValueError("document must be OfficialTariffDocument")
        object.__setattr__(self, "product_name", _non_empty(self.product_name, "product_name"))
        if not isinstance(self.valid_from, date):
            raise ValueError("valid_from must be a date")
        if self.valid_to is not None:
            if not isinstance(self.valid_to, date):
                raise ValueError("valid_to must be a date")
            if self.valid_to < self.valid_from:
                raise ValueError("candidate validity end must not precede start")
        if isinstance(self.match_score, bool) or not isinstance(self.match_score, int):
            raise ValueError("match_score must be an integer")
        if not 0 <= self.match_score <= 100:
            raise ValueError("match_score must be between 0 and 100")
        reasons = tuple(self.match_reasons)
        if any(not isinstance(item, str) or not item.strip() for item in reasons):
            raise ValueError("match_reasons must contain non-empty strings")
        object.__setattr__(self, "match_reasons", reasons)
        if self.price_scope not in PRICE_SCOPES:
            raise ValueError(f"unsupported price_scope: {self.price_scope}")


@runtime_checkable
class SupplierTariffAdapter(Protocol):
    """Adapter interface for one supplier's official public price-list sources."""

    supplier: str
    official_domains: tuple[str, ...]

    async def async_discover(self, query: TariffSourceQuery) -> Iterable[TariffDocumentCandidate]:
        ...


class TariffAdapterRegistry:
    """Registry that enforces supplier identity and official-domain boundaries."""

    def __init__(self) -> None:
        self._adapters: dict[str, SupplierTariffAdapter] = {}
        self._domains: dict[str, tuple[str, ...]] = {}

    def register(self, adapter: SupplierTariffAdapter) -> None:
        supplier = _supplier_slug(getattr(adapter, "supplier", ""))
        domains_raw = getattr(adapter, "official_domains", ())
        if not isinstance(domains_raw, tuple) or not domains_raw:
            raise ValueError("adapter official_domains must be a non-empty tuple")
        domains = tuple(dict.fromkeys(_official_domain(item) for item in domains_raw))
        if supplier in self._adapters:
            raise ValueError(f"tariff adapter already registered: {supplier}")
        if not callable(getattr(adapter, "async_discover", None)):
            raise ValueError("adapter must implement async_discover")
        self._adapters[supplier] = adapter
        self._domains[supplier] = domains

    def supported_suppliers(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))

    def for_supplier(self, supplier: str) -> SupplierTariffAdapter:
        slug = _supplier_slug(supplier)
        try:
            return self._adapters[slug]
        except KeyError as err:
            raise LookupError(f"no tariff adapter registered for supplier: {slug}") from err

    async def async_discover_verified(self, query: TariffSourceQuery) -> tuple[TariffDocumentCandidate, ...]:
        if not isinstance(query, TariffSourceQuery):
            raise ValueError("query must be TariffSourceQuery")
        adapter = self.for_supplier(query.supplier)
        candidates = tuple(await adapter.async_discover(query))
        domains = self._domains[query.supplier]
        for candidate in candidates:
            if not isinstance(candidate, TariffDocumentCandidate):
                raise ValueError("adapter returned an invalid tariff candidate")
            if candidate.document.supplier != query.supplier:
                raise ValueError("candidate supplier does not match query supplier")
            host = _url_host(candidate.document.source_url)
            if not any(_host_matches_domain(host, domain) for domain in domains):
                raise ValueError(
                    f"candidate source is outside official domains for {query.supplier}: {host}"
                )
        return candidates
