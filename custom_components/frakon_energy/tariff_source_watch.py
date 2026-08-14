"""Fail-closed source-watch state for confirmed all-in tariff documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from .all_in_catalog import PersistedAllInTariff
from .tariff_sources import (
    PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    OfficialTariffDocument,
)

TARIFF_SOURCE_WATCH_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SUPPLIER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

STATUS_NOT_MODIFIED = "not_modified"
STATUS_UNCHANGED_HASH = "unchanged_hash"
STATUS_CHANGE_DETECTED = "change_detected"
STATUS_ERROR = "error"
_CHECK_STATUSES = frozenset(
    {
        STATUS_NOT_MODIFIED,
        STATUS_UNCHANGED_HASH,
        STATUS_CHANGE_DETECTED,
        STATUS_ERROR,
    }
)


def _non_empty(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value.strip()


def _supplier_slug(value: str) -> str:
    supplier = _non_empty(value, "supplier").lower()
    if not _SUPPLIER_RE.fullmatch(supplier):
        raise ValueError("supplier must be a lowercase slug")
    return supplier


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


def _sha256(value: str, field: str) -> str:
    checksum = _non_empty(value, field).lower()
    if not _SHA256_RE.fullmatch(checksum):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return checksum


def _optional_non_empty(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    return _non_empty(value, field)


def _date_from_value(value: Any, field: str) -> date:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 date")
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"{field} must be an ISO-8601 date") from err


def _datetime_from_value(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 datetime")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"{field} must be an ISO-8601 datetime") from err
    if result.tzinfo is None:
        raise ValueError(f"{field} must include timezone information")
    return result


@dataclass(frozen=True, slots=True)
class TariffSourceWatch:
    """One confirmed supplier document watched for a newer source version.

    `active_sha256` is immutable pricing authority from the confirmed tariff.
    Validators are operational hints only; they never alter the active checksum.
    """

    supplier: str
    product_name: str
    source_name: str
    document_name: str
    source_url: str
    valid_from: date
    valid_to: date | None
    active_sha256: str
    document_date: date | None = None
    etag: str | None = None
    last_modified: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "supplier", _supplier_slug(self.supplier))
        object.__setattr__(self, "product_name", _non_empty(self.product_name, "product_name"))
        object.__setattr__(self, "source_name", _non_empty(self.source_name, "source_name"))
        object.__setattr__(self, "document_name", _non_empty(self.document_name, "document_name"))
        object.__setattr__(self, "source_url", _https_url(self.source_url))
        if not isinstance(self.valid_from, date):
            raise ValueError("valid_from must be a date")
        if self.valid_to is not None:
            if not isinstance(self.valid_to, date):
                raise ValueError("valid_to must be a date")
            if self.valid_to < self.valid_from:
                raise ValueError("watch validity end must not precede start")
        object.__setattr__(
            self,
            "active_sha256",
            _sha256(self.active_sha256, "active_sha256"),
        )
        if self.document_date is not None and not isinstance(self.document_date, date):
            raise ValueError("document_date must be a date")
        object.__setattr__(self, "etag", _optional_non_empty(self.etag, "etag"))
        object.__setattr__(
            self,
            "last_modified",
            _optional_non_empty(self.last_modified, "last_modified"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TARIFF_SOURCE_WATCH_SCHEMA_VERSION,
            "supplier": self.supplier,
            "product_name": self.product_name,
            "source_name": self.source_name,
            "document_name": self.document_name,
            "source_url": self.source_url,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to is not None else None,
            "active_sha256": self.active_sha256,
            "document_date": (
                self.document_date.isoformat() if self.document_date is not None else None
            ),
            "etag": self.etag,
            "last_modified": self.last_modified,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TariffSourceWatch:
        if not isinstance(value, Mapping):
            raise ValueError("tariff source watch must be an object")
        if value.get("schema_version") != TARIFF_SOURCE_WATCH_SCHEMA_VERSION:
            raise ValueError("unsupported tariff source watch schema version")
        valid_to_raw = value.get("valid_to")
        document_date_raw = value.get("document_date")
        return cls(
            supplier=value.get("supplier"),
            product_name=value.get("product_name"),
            source_name=value.get("source_name"),
            document_name=value.get("document_name"),
            source_url=value.get("source_url"),
            valid_from=_date_from_value(value.get("valid_from"), "valid_from"),
            valid_to=(
                _date_from_value(valid_to_raw, "valid_to")
                if valid_to_raw not in (None, "")
                else None
            ),
            active_sha256=value.get("active_sha256"),
            document_date=(
                _date_from_value(document_date_raw, "document_date")
                if document_date_raw not in (None, "")
                else None
            ),
            etag=value.get("etag"),
            last_modified=value.get("last_modified"),
        )


def tariff_source_watch_fingerprint(watch: TariffSourceWatch) -> str:
    """Stable watch-target identity that ignores mutable checksum/HTTP validators."""
    if not isinstance(watch, TariffSourceWatch):
        raise ValueError("watch must be TariffSourceWatch")
    payload = {
        "supplier": watch.supplier,
        "product_name": watch.product_name,
        "source_url": watch.source_url,
        "valid_from": watch.valid_from.isoformat(),
        "valid_to": watch.valid_to.isoformat() if watch.valid_to is not None else None,
        "price_scope": PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_watch_from_confirmed_all_in(
    item: PersistedAllInTariff,
    *,
    supplier: str,
    etag: str | None = None,
    last_modified: str | None = None,
) -> TariffSourceWatch:
    """Create a source watch only from an explicitly confirmed all-in tariff."""
    if not isinstance(item, PersistedAllInTariff):
        raise ValueError("item must be PersistedAllInTariff")
    if not item.confirmed:
        raise ValueError("tariff source watch requires a confirmed all-in tariff")

    supplier_evidence = item.assembly.provenance.evidence_for_scope(
        PRICE_SCOPE_SUPPLIER_COMMERCIAL
    )
    if len(supplier_evidence) != 1:
        raise ValueError("confirmed all-in tariff must have exactly one supplier source to watch")
    evidence = supplier_evidence[0]
    if evidence.checksum is None:
        raise ValueError("confirmed supplier evidence requires a checksum before source watch")

    return TariffSourceWatch(
        supplier=supplier,
        product_name=item.assembly.product_name,
        source_name=evidence.source_name,
        document_name=evidence.document_name,
        source_url=evidence.source_url,
        valid_from=evidence.valid_from,
        valid_to=evidence.valid_to,
        active_sha256=evidence.checksum,
        document_date=evidence.document_date,
        etag=etag,
        last_modified=last_modified,
    )


@dataclass(frozen=True, slots=True)
class TariffSourceCheckResult:
    """Observation from one watch check; never mutates active pricing authority."""

    watch_fingerprint: str
    status: str
    checked_at: datetime
    active_sha256: str
    observed_sha256: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None
    active_unchanged: bool = True
    requires_confirmation: bool = False
    persistence_performed: bool = False
    activation_performed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "watch_fingerprint",
            _sha256(self.watch_fingerprint, "watch_fingerprint"),
        )
        if self.status not in _CHECK_STATUSES:
            raise ValueError(f"unsupported tariff source check status: {self.status}")
        if not isinstance(self.checked_at, datetime) or self.checked_at.tzinfo is None:
            raise ValueError("checked_at must be a timezone-aware datetime")
        object.__setattr__(
            self,
            "active_sha256",
            _sha256(self.active_sha256, "active_sha256"),
        )
        if self.observed_sha256 is not None:
            object.__setattr__(
                self,
                "observed_sha256",
                _sha256(self.observed_sha256, "observed_sha256"),
            )
        object.__setattr__(self, "etag", _optional_non_empty(self.etag, "etag"))
        object.__setattr__(
            self,
            "last_modified",
            _optional_non_empty(self.last_modified, "last_modified"),
        )
        object.__setattr__(self, "error", _optional_non_empty(self.error, "error"))

        if self.active_unchanged is not True:
            raise ValueError("source checks must never mutate active pricing authority")
        if self.persistence_performed is not False or self.activation_performed is not False:
            raise ValueError("source check results cannot persist or activate prices")

        if self.status == STATUS_NOT_MODIFIED:
            if self.observed_sha256 is not None or self.error is not None:
                raise ValueError("not_modified result cannot contain body hash or error")
            if self.requires_confirmation:
                raise ValueError("not_modified result cannot require confirmation")
        elif self.status == STATUS_UNCHANGED_HASH:
            if self.observed_sha256 != self.active_sha256 or self.error is not None:
                raise ValueError("unchanged_hash must observe the active checksum")
            if self.requires_confirmation:
                raise ValueError("unchanged_hash cannot require confirmation")
        elif self.status == STATUS_CHANGE_DETECTED:
            if self.observed_sha256 is None or self.observed_sha256 == self.active_sha256:
                raise ValueError("change_detected requires a different observed checksum")
            if self.error is not None or self.requires_confirmation is not True:
                raise ValueError("change_detected must require confirmation and contain no error")
        elif self.status == STATUS_ERROR:
            if self.error is None or self.observed_sha256 is not None:
                raise ValueError("error result requires error text and no observed checksum")
            if self.requires_confirmation:
                raise ValueError("error result cannot require confirmation")

    def as_dict(self) -> dict[str, Any]:
        return {
            "watch_fingerprint": self.watch_fingerprint,
            "status": self.status,
            "checked_at": self.checked_at.isoformat(),
            "active_sha256": self.active_sha256,
            "observed_sha256": self.observed_sha256,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "error": self.error,
            "active_unchanged": self.active_unchanged,
            "requires_confirmation": self.requires_confirmation,
            "persistence_performed": self.persistence_performed,
            "activation_performed": self.activation_performed,
        }


def tariff_source_not_modified(
    watch: TariffSourceWatch,
    *,
    checked_at: datetime,
    etag: str | None = None,
    last_modified: str | None = None,
) -> TariffSourceCheckResult:
    if not isinstance(watch, TariffSourceWatch):
        raise ValueError("watch must be TariffSourceWatch")
    return TariffSourceCheckResult(
        watch_fingerprint=tariff_source_watch_fingerprint(watch),
        status=STATUS_NOT_MODIFIED,
        checked_at=checked_at,
        active_sha256=watch.active_sha256,
        etag=etag if etag is not None else watch.etag,
        last_modified=(
            last_modified if last_modified is not None else watch.last_modified
        ),
    )


def evaluate_tariff_source_download(
    watch: TariffSourceWatch,
    *,
    document: OfficialTariffDocument,
    checked_at: datetime,
) -> TariffSourceCheckResult:
    """Compare newly downloaded bytes with the active confirmed document hash."""
    if not isinstance(watch, TariffSourceWatch):
        raise ValueError("watch must be TariffSourceWatch")
    if not isinstance(document, OfficialTariffDocument):
        raise ValueError("document must be OfficialTariffDocument")
    if document.supplier != watch.supplier:
        raise ValueError("observed tariff document supplier does not match source watch")
    if document.source_url != watch.source_url:
        raise ValueError("observed tariff document URL does not match source watch")
    if document.sha256 is None:
        raise ValueError("observed tariff document requires a SHA-256 checksum")

    changed = document.sha256 != watch.active_sha256
    return TariffSourceCheckResult(
        watch_fingerprint=tariff_source_watch_fingerprint(watch),
        status=STATUS_CHANGE_DETECTED if changed else STATUS_UNCHANGED_HASH,
        checked_at=checked_at,
        active_sha256=watch.active_sha256,
        observed_sha256=document.sha256,
        etag=document.etag,
        last_modified=document.last_modified,
        requires_confirmation=changed,
    )


def tariff_source_check_error(
    watch: TariffSourceWatch,
    *,
    checked_at: datetime,
    error: str,
) -> TariffSourceCheckResult:
    if not isinstance(watch, TariffSourceWatch):
        raise ValueError("watch must be TariffSourceWatch")
    return TariffSourceCheckResult(
        watch_fingerprint=tariff_source_watch_fingerprint(watch),
        status=STATUS_ERROR,
        checked_at=checked_at,
        active_sha256=watch.active_sha256,
        error=error,
    )
