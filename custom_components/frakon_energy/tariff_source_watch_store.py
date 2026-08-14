"""Durable config-entry options state for tariff source watches."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Mapping

from .tariff_source_watch import (
    STATUS_CHANGE_DETECTED,
    STATUS_ERROR,
    STATUS_NOT_MODIFIED,
    STATUS_UNCHANGED_HASH,
    TariffSourceCheckResult,
    TariffSourceWatch,
    tariff_source_watch_fingerprint,
)

TARIFF_SOURCE_WATCH_STORE_SCHEMA_VERSION = 1
OPTION_TARIFF_SOURCE_WATCHES = "tariff_source_watches"


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


def _check_from_dict(value: Mapping[str, Any]) -> TariffSourceCheckResult:
    if not isinstance(value, Mapping):
        raise ValueError("last tariff source check must be an object")
    checked_at = _datetime_from_value(value.get("checked_at"), "checked_at")
    return TariffSourceCheckResult(
        watch_fingerprint=value.get("watch_fingerprint"),
        status=value.get("status"),
        checked_at=checked_at,
        active_sha256=value.get("active_sha256"),
        observed_sha256=value.get("observed_sha256"),
        etag=value.get("etag"),
        last_modified=value.get("last_modified"),
        error=value.get("error"),
        active_unchanged=value.get("active_unchanged", True),
        requires_confirmation=value.get("requires_confirmation", False),
        persistence_performed=value.get("persistence_performed", False),
        activation_performed=value.get("activation_performed", False),
    )


@dataclass(frozen=True, slots=True)
class TariffSourceWatchRecord:
    """Operational state for one watch target without active-price mutation rights."""

    watch: TariffSourceWatch
    last_check: TariffSourceCheckResult | None = None
    pending_sha256: str | None = None
    pending_detected_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.watch, TariffSourceWatch):
            raise ValueError("watch must be TariffSourceWatch")
        fingerprint = tariff_source_watch_fingerprint(self.watch)
        if self.last_check is not None:
            if not isinstance(self.last_check, TariffSourceCheckResult):
                raise ValueError("last_check must be TariffSourceCheckResult")
            if self.last_check.watch_fingerprint != fingerprint:
                raise ValueError("last_check does not belong to tariff source watch")
            if self.last_check.active_sha256 != self.watch.active_sha256:
                raise ValueError("last_check active checksum does not match source watch")
        if (self.pending_sha256 is None) != (self.pending_detected_at is None):
            raise ValueError("pending checksum and detection time must be stored together")
        if self.pending_sha256 is not None:
            if (
                len(self.pending_sha256) != 64
                or any(char not in "0123456789abcdef" for char in self.pending_sha256)
            ):
                raise ValueError("pending_sha256 must be a lowercase SHA-256 digest")
            if self.pending_sha256 == self.watch.active_sha256:
                raise ValueError("pending checksum must differ from active checksum")
            if (
                not isinstance(self.pending_detected_at, datetime)
                or self.pending_detected_at.tzinfo is None
            ):
                raise ValueError("pending_detected_at must be a timezone-aware datetime")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TARIFF_SOURCE_WATCH_STORE_SCHEMA_VERSION,
            "watch": self.watch.as_dict(),
            "last_check": self.last_check.as_dict() if self.last_check is not None else None,
            "pending_sha256": self.pending_sha256,
            "pending_detected_at": (
                self.pending_detected_at.isoformat()
                if self.pending_detected_at is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TariffSourceWatchRecord:
        if not isinstance(value, Mapping):
            raise ValueError("tariff source watch record must be an object")
        if value.get("schema_version") != TARIFF_SOURCE_WATCH_STORE_SCHEMA_VERSION:
            raise ValueError("unsupported tariff source watch store schema version")
        watch_raw = value.get("watch")
        if not isinstance(watch_raw, Mapping):
            raise ValueError("tariff source watch record requires watch object")
        last_check_raw = value.get("last_check")
        if last_check_raw is not None and not isinstance(last_check_raw, Mapping):
            raise ValueError("last tariff source check must be an object")
        pending_at_raw = value.get("pending_detected_at")
        return cls(
            watch=TariffSourceWatch.from_dict(watch_raw),
            last_check=(
                _check_from_dict(last_check_raw)
                if last_check_raw is not None
                else None
            ),
            pending_sha256=value.get("pending_sha256"),
            pending_detected_at=(
                _datetime_from_value(pending_at_raw, "pending_detected_at")
                if pending_at_raw not in (None, "")
                else None
            ),
        )


def tariff_source_watch_records_from_options(
    options: Mapping[str, Any],
) -> tuple[TariffSourceWatchRecord, ...]:
    """Load durable watch state and reject duplicate watch targets."""
    raw = options.get(OPTION_TARIFF_SOURCE_WATCHES, [])
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("tariff_source_watches must be a list")

    records: list[TariffSourceWatchRecord] = []
    seen: set[str] = set()
    for raw_record in raw:
        if not isinstance(raw_record, Mapping):
            raise ValueError("each tariff source watch record must be an object")
        record = TariffSourceWatchRecord.from_dict(raw_record)
        fingerprint = tariff_source_watch_fingerprint(record.watch)
        if fingerprint in seen:
            raise ValueError(f"duplicate tariff source watch fingerprint: {fingerprint}")
        seen.add(fingerprint)
        records.append(record)
    return tuple(records)


def append_tariff_source_watch(
    options: Mapping[str, Any],
    watch: TariffSourceWatch,
) -> dict[str, Any]:
    """Append one watch target; never replace an existing active checksum."""
    if not isinstance(watch, TariffSourceWatch):
        raise ValueError("watch must be TariffSourceWatch")
    records = list(tariff_source_watch_records_from_options(options))
    fingerprint = tariff_source_watch_fingerprint(watch)
    for record in records:
        if tariff_source_watch_fingerprint(record.watch) != fingerprint:
            continue
        if record.watch.active_sha256 != watch.active_sha256:
            raise ValueError(
                "existing tariff source watch active checksum cannot be replaced by append"
            )
        return dict(options)

    records.append(TariffSourceWatchRecord(watch=watch))
    updated = dict(options)
    updated[OPTION_TARIFF_SOURCE_WATCHES] = [record.as_dict() for record in records]
    return updated


def tariff_source_watch_record_from_options(
    options: Mapping[str, Any],
    watch_fingerprint: str,
) -> TariffSourceWatchRecord:
    """Return exactly one stored source-watch record by stable target identity."""
    for record in tariff_source_watch_records_from_options(options):
        if tariff_source_watch_fingerprint(record.watch) == watch_fingerprint:
            return record
    raise LookupError(f"tariff source watch not found: {watch_fingerprint}")


def record_tariff_source_check(
    options: Mapping[str, Any],
    result: TariffSourceCheckResult,
) -> dict[str, Any]:
    """Persist one operational check without changing the active document checksum."""
    if not isinstance(result, TariffSourceCheckResult):
        raise ValueError("result must be TariffSourceCheckResult")

    records = list(tariff_source_watch_records_from_options(options))
    matched = False
    for index, record in enumerate(records):
        fingerprint = tariff_source_watch_fingerprint(record.watch)
        if fingerprint != result.watch_fingerprint:
            continue
        matched = True
        if result.active_sha256 != record.watch.active_sha256:
            raise ValueError("source check active checksum does not match stored watch")

        updated_watch = replace(
            record.watch,
            etag=result.etag if result.etag is not None else record.watch.etag,
            last_modified=(
                result.last_modified
                if result.last_modified is not None
                else record.watch.last_modified
            ),
        )
        pending_sha256 = record.pending_sha256
        pending_detected_at = record.pending_detected_at
        if result.status == STATUS_CHANGE_DETECTED:
            pending_sha256 = result.observed_sha256
            pending_detected_at = result.checked_at
        elif result.status == STATUS_UNCHANGED_HASH:
            pending_sha256 = None
            pending_detected_at = None
        elif result.status in (STATUS_NOT_MODIFIED, STATUS_ERROR):
            pass
        else:
            raise ValueError(f"unsupported source check status: {result.status}")

        records[index] = TariffSourceWatchRecord(
            watch=updated_watch,
            last_check=result,
            pending_sha256=pending_sha256,
            pending_detected_at=pending_detected_at,
        )
        break

    if not matched:
        raise LookupError(f"tariff source watch not found: {result.watch_fingerprint}")

    updated = dict(options)
    updated[OPTION_TARIFF_SOURCE_WATCHES] = [record.as_dict() for record in records]
    return updated
