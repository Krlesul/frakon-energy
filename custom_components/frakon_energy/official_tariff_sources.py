from __future__ import annotations

from enum import StrEnum
from urllib.parse import urlsplit

# Discovery roots are deliberately supplier-owned landing pages, not individual
# PDFs. Supplier sites can rotate document URLs without widening the trust
# boundary to search engines, mirrors, URL shorteners, or third-party hosts.
OFFICIAL_PRICE_LIST_LANDING_URLS: dict[str, str] = {
    "cez": "https://www.cez.cz/cs/podpora/ceniky",
    "eon": "https://www.eon.cz/domacnosti/zakaznicka-pece/ceniky/",
    "pre": "https://www.pre.cz/cs/domacnosti/elektrina/prehled-produktu/aktualni/",
    "mnd": "https://www.mnd.cz/Dokumenty-ke-stazeni",
}

OFFICIAL_SUPPLIER_ROOT_DOMAINS: dict[str, str] = {
    "cez": "cez.cz",
    "eon": "eon.cz",
    "pre": "pre.cz",
    "mnd": "mnd.cz",
}


def _supplier_value(supplier: str | StrEnum) -> str:
    value = supplier.value if isinstance(supplier, StrEnum) else supplier
    if not isinstance(value, str) or not value.strip():
        raise ValueError("supplier is required")
    return value.strip().lower()


def official_price_list_landing_url(supplier: str | StrEnum) -> str:
    """Return the trusted discovery root for a supported supplier."""
    value = _supplier_value(supplier)
    try:
        return OFFICIAL_PRICE_LIST_LANDING_URLS[value]
    except KeyError as err:
        raise LookupError(f"no official automated tariff source for supplier: {value}") from err


def is_official_supplier_source_url(supplier: str | StrEnum, url: str) -> bool:
    """Return whether URL stays inside the supplier-owned HTTPS trust boundary."""
    value = _supplier_value(supplier)
    root_domain = OFFICIAL_SUPPLIER_ROOT_DOMAINS.get(value)
    if root_domain is None or not isinstance(url, str) or not url.strip():
        return False

    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError:
        return False

    if parsed.scheme.lower() != "https":
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    if port not in (None, 443):
        return False

    hostname = (parsed.hostname or "").rstrip(".").lower()
    if not hostname:
        return False
    return hostname == root_domain or hostname.endswith(f".{root_domain}")


def require_official_supplier_source_url(supplier: str | StrEnum, url: str) -> str:
    """Validate and normalize an automated tariff source URL or fail closed."""
    normalized = url.strip() if isinstance(url, str) else ""
    if not is_official_supplier_source_url(supplier, normalized):
        raise ValueError("tariff source URL is outside the official supplier trust boundary")
    return normalized
