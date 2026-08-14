"""Regulated electricity pricing components for FRAKON Energy.

This module deliberately models the regulated part of a customer tariff separately
from supplier-commercial prices.  A regulated bundle is not an all-in tariff by
itself and must retain its own source evidence before any later composition step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import re
from urllib.parse import urlparse

from .pricing import FixedPriceComponent, PriceComponentKind, VariablePriceComponent
from .tariff_sources import PRICE_SCOPE_REGULATED

_TARIFF_RE = re.compile(r"^D\d{2}d$")
_BREAKER_RE = re.compile(r"^(?:1|3)x[1-9]\d*A$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_REGULATED_VARIABLE_KINDS = frozenset(
    {
        PriceComponentKind.DISTRIBUTION,
        PriceComponentKind.POZE,
        PriceComponentKind.SYSTEM_SERVICES,
        PriceComponentKind.ELECTRICITY_TAX,
        PriceComponentKind.MARKET,
    }
)
_REGULATED_FIXED_KINDS = frozenset(
    {
        PriceComponentKind.BREAKER_FIXED,
        PriceComponentKind.DISTRIBUTION_FIXED,
        PriceComponentKind.OTHER_FIXED,
    }
)


def _non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value.strip()


def _normalize_tariff(value: str) -> str:
    tariff = _non_empty(value, "distribution_tariff")
    tariff = tariff[0].upper() + tariff[1:-1] + tariff[-1].lower()
    if not _TARIFF_RE.fullmatch(tariff):
        raise ValueError("distribution_tariff must use a code such as D25d")
    return tariff


def _validate_https_url(value: str) -> str:
    url = _non_empty(value, "source_url")
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("source_url must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("source_url must not contain embedded credentials")
    try:
        port = parsed.port
    except ValueError as err:
        raise ValueError("source_url contains an invalid port") from err
    if port not in (None, 443):
        raise ValueError("source_url must not use a nonstandard HTTPS port")
    return url


@dataclass(frozen=True, slots=True)
class RegulatedTariffBundle:
    """One immutable regulated price version for a tariff and breaker combination.

    The bundle can only contain component kinds that belong to the regulated side
    of the electricity bill.  Supplier commodity and supplier standing charges are
    therefore rejected at construction time.
    """

    distributor: str
    distribution_tariff: str
    breaker_code: str
    valid_from: date
    variable_components: tuple[VariablePriceComponent, ...]
    fixed_components: tuple[FixedPriceComponent, ...]
    source_url: str
    valid_to: date | None = None
    document_date: date | None = None
    checksum: str | None = None
    confirmed: bool = False
    price_scope: str = field(default=PRICE_SCOPE_REGULATED, init=False)
    all_in_ready: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "distributor", _non_empty(self.distributor, "distributor"))
        object.__setattr__(self, "distribution_tariff", _normalize_tariff(self.distribution_tariff))
        breaker = _non_empty(self.breaker_code, "breaker_code")
        if not _BREAKER_RE.fullmatch(breaker):
            raise ValueError("breaker_code must use a code such as 3x25A")
        object.__setattr__(self, "breaker_code", breaker)

        if not isinstance(self.valid_from, date):
            raise ValueError("valid_from must be a date")
        if self.valid_to is not None:
            if not isinstance(self.valid_to, date):
                raise ValueError("valid_to must be a date")
            if self.valid_to < self.valid_from:
                raise ValueError("regulated validity end must not precede start")
        if self.document_date is not None and not isinstance(self.document_date, date):
            raise ValueError("document_date must be a date")
        if not isinstance(self.confirmed, bool):
            raise ValueError("confirmed must be boolean")

        object.__setattr__(self, "source_url", _validate_https_url(self.source_url))
        if self.checksum is not None:
            checksum = _non_empty(self.checksum, "checksum").lower()
            if not _SHA256_RE.fullmatch(checksum):
                raise ValueError("checksum must be a lowercase SHA-256 digest")
            object.__setattr__(self, "checksum", checksum)

        variable = tuple(self.variable_components)
        fixed = tuple(self.fixed_components)
        if not variable:
            raise ValueError("regulated bundle must contain variable components")
        if not fixed:
            raise ValueError("regulated bundle must contain fixed components")
        if not all(isinstance(item, VariablePriceComponent) for item in variable):
            raise ValueError("variable_components contains an invalid item")
        if not all(isinstance(item, FixedPriceComponent) for item in fixed):
            raise ValueError("fixed_components contains an invalid item")
        if any(item.kind not in _REGULATED_VARIABLE_KINDS for item in variable):
            raise ValueError("regulated variable components contain a supplier or unsupported kind")
        if any(item.kind not in _REGULATED_FIXED_KINDS for item in fixed):
            raise ValueError("regulated fixed components contain a supplier or unsupported kind")

        names = [item.name for item in (*variable, *fixed)]
        if len(set(names)) != len(names):
            raise ValueError("regulated component names must be unique")
        object.__setattr__(self, "variable_components", variable)
        object.__setattr__(self, "fixed_components", fixed)

    def applies_on(self, day: date) -> bool:
        if not isinstance(day, date):
            raise ValueError("day must be a date")
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)

    def matches_customer_tariff(
        self,
        *,
        distribution_tariff: str,
        breaker_code: str,
        day: date,
        require_confirmation: bool = True,
    ) -> bool:
        """Return true only for an exact tariff/breaker/date match.

        Confirmation is required by default so discovered or parsed regulated data
        cannot silently become active before a customer or a later trusted workflow
        explicitly accepts the version.
        """

        tariff = _normalize_tariff(distribution_tariff)
        breaker = _non_empty(breaker_code, "breaker_code")
        if not _BREAKER_RE.fullmatch(breaker):
            raise ValueError("breaker_code must use a code such as 3x25A")
        if require_confirmation and not self.confirmed:
            return False
        return (
            self.distribution_tariff == tariff
            and self.breaker_code == breaker
            and self.applies_on(day)
        )
