"""Multi-source price evidence for auditable all-in electricity tariffs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from .tariff_sources import PRICE_SCOPE_REGULATED, PRICE_SCOPE_SUPPLIER_COMMERCIAL

TARIFF_PROVENANCE_SCHEMA_VERSION = 1
_ALLOWED_SOURCE_SCOPES = frozenset(
    {PRICE_SCOPE_SUPPLIER_COMMERCIAL, PRICE_SCOPE_REGULATED}
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _non_empty(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value.strip()


def _date_from_value(value: Any, field: str) -> date:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 date")
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"{field} must be an ISO-8601 date") from err


def _https_url(value: str) -> str:
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
class PriceEvidence:
    """One immutable official document contributing part of a tariff price."""

    scope: str
    source_name: str
    document_name: str
    source_url: str
    valid_from: date
    valid_to: date | None = None
    document_date: date | None = None
    checksum: str | None = None

    def __post_init__(self) -> None:
        if self.scope not in _ALLOWED_SOURCE_SCOPES:
            raise ValueError(f"unsupported price evidence scope: {self.scope}")
        object.__setattr__(self, "source_name", _non_empty(self.source_name, "source_name"))
        object.__setattr__(self, "document_name", _non_empty(self.document_name, "document_name"))
        object.__setattr__(self, "source_url", _https_url(self.source_url))
        if not isinstance(self.valid_from, date):
            raise ValueError("valid_from must be a date")
        if self.valid_to is not None:
            if not isinstance(self.valid_to, date):
                raise ValueError("valid_to must be a date")
            if self.valid_to < self.valid_from:
                raise ValueError("evidence validity end must not precede start")
        if self.document_date is not None and not isinstance(self.document_date, date):
            raise ValueError("document_date must be a date")
        if self.checksum is not None:
            checksum = _non_empty(self.checksum, "checksum").lower()
            if not _SHA256_RE.fullmatch(checksum):
                raise ValueError("checksum must be a lowercase SHA-256 digest")
            object.__setattr__(self, "checksum", checksum)

    def applies_on(self, day: date) -> bool:
        if not isinstance(day, date):
            raise ValueError("day must be a date")
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "source_name": self.source_name,
            "document_name": self.document_name,
            "source_url": self.source_url,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to is not None else None,
            "document_date": (
                self.document_date.isoformat() if self.document_date is not None else None
            ),
            "checksum": self.checksum,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PriceEvidence:
        if not isinstance(value, Mapping):
            raise ValueError("price evidence must be an object")
        valid_to_raw = value.get("valid_to")
        document_date_raw = value.get("document_date")
        return cls(
            scope=value.get("scope"),
            source_name=value.get("source_name"),
            document_name=value.get("document_name"),
            source_url=value.get("source_url"),
            valid_from=_date_from_value(value.get("valid_from"), "valid_from"),
            valid_to=(
                _date_from_value(valid_to_raw, "valid_to")
                if valid_to_raw not in (None, "")
                else None
            ),
            document_date=(
                _date_from_value(document_date_raw, "document_date")
                if document_date_raw not in (None, "")
                else None
            ),
            checksum=value.get("checksum"),
        )


def price_evidence_fingerprint(evidence: PriceEvidence) -> str:
    if not isinstance(evidence, PriceEvidence):
        raise ValueError("evidence must be PriceEvidence")
    encoded = json.dumps(
        evidence.as_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class MultiSourceTariffProvenance:
    """Complete provenance for an all-in tariff assembled from independent sources.

    At least one supplier-commercial and one regulated evidence document are
    required.  Evidence order is canonicalized, so the identity of the provenance
    does not depend on discovery order.
    """

    evidence: tuple[PriceEvidence, ...]

    def __post_init__(self) -> None:
        items = tuple(self.evidence)
        if len(items) < 2:
            raise ValueError("all-in provenance requires at least two evidence documents")
        if not all(isinstance(item, PriceEvidence) for item in items):
            raise ValueError("evidence contains an invalid item")

        scopes = {item.scope for item in items}
        missing = _ALLOWED_SOURCE_SCOPES - scopes
        if missing:
            raise ValueError(
                "all-in provenance requires supplier-commercial and regulated evidence"
            )

        fingerprinted = [(price_evidence_fingerprint(item), item) for item in items]
        fingerprints = [fingerprint for fingerprint, _ in fingerprinted]
        if len(set(fingerprints)) != len(fingerprints):
            raise ValueError("duplicate price evidence is not allowed")
        fingerprinted.sort(key=lambda pair: pair[0])
        object.__setattr__(self, "evidence", tuple(item for _, item in fingerprinted))

        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("price evidence validity periods do not overlap")

    @property
    def valid_from(self) -> date:
        return max(item.valid_from for item in self.evidence)

    @property
    def valid_to(self) -> date | None:
        bounded = [item.valid_to for item in self.evidence if item.valid_to is not None]
        return min(bounded) if bounded else None

    @property
    def complete_for_all_in(self) -> bool:
        return True

    def covers_day(self, day: date) -> bool:
        if not isinstance(day, date):
            raise ValueError("day must be a date")
        return all(item.applies_on(day) for item in self.evidence)

    def evidence_for_scope(self, scope: str) -> tuple[PriceEvidence, ...]:
        if scope not in _ALLOWED_SOURCE_SCOPES:
            raise ValueError(f"unsupported price evidence scope: {scope}")
        return tuple(item for item in self.evidence if item.scope == scope)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TARIFF_PROVENANCE_SCHEMA_VERSION,
            "evidence": [item.as_dict() for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> MultiSourceTariffProvenance:
        if not isinstance(value, Mapping):
            raise ValueError("tariff provenance must be an object")
        if value.get("schema_version") != TARIFF_PROVENANCE_SCHEMA_VERSION:
            raise ValueError("unsupported tariff provenance schema version")
        raw = value.get("evidence")
        if not isinstance(raw, list):
            raise ValueError("tariff provenance evidence must be a list")
        return cls(tuple(PriceEvidence.from_dict(item) for item in raw))


def tariff_provenance_fingerprint(provenance: MultiSourceTariffProvenance) -> str:
    if not isinstance(provenance, MultiSourceTariffProvenance):
        raise ValueError("provenance must be MultiSourceTariffProvenance")
    encoded = json.dumps(
        provenance.as_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
