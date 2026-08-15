"""Immutable authority metadata for durable all-in tariff versions.

The all-in tariff fingerprint identifies price content and provenance.  How those
supplier-commercial values were obtained is a separate authority dimension and
must never silently change an existing tariff identity.

New automatic parser paths use ``verified_parser``.  A later manual fallback can
use ``manual_user_entry`` while still pointing at the exact same immutable
all-in catalog record and official document provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Mapping

from .all_in_catalog import all_in_tariff_fingerprint, all_in_tariffs_from_options

ALL_IN_TARIFF_AUTHORITY_SCHEMA_VERSION = 1
OPTION_ALL_IN_TARIFF_AUTHORITIES = "all_in_tariff_authorities"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AllInTariffAuthorityMethod(StrEnum):
    """How supplier-commercial values entered one durable all-in version."""

    VERIFIED_PARSER = "verified_parser"
    MANUAL_USER_ENTRY = "manual_user_entry"


def _fingerprint(value: Any) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(
            "all_in_tariff_fingerprint must be a lowercase SHA-256 hex digest"
        )
    return value


def _method(value: Any) -> AllInTariffAuthorityMethod:
    if isinstance(value, AllInTariffAuthorityMethod):
        return value
    try:
        return AllInTariffAuthorityMethod(value)
    except (TypeError, ValueError) as err:
        raise ValueError("unsupported all-in tariff authority method") from err


@dataclass(frozen=True, slots=True)
class AllInTariffAuthority:
    """One immutable authority label attached to an all-in tariff fingerprint."""

    all_in_tariff_fingerprint: str
    method: AllInTariffAuthorityMethod

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "all_in_tariff_fingerprint",
            _fingerprint(self.all_in_tariff_fingerprint),
        )
        object.__setattr__(self, "method", _method(self.method))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ALL_IN_TARIFF_AUTHORITY_SCHEMA_VERSION,
            "all_in_tariff_fingerprint": self.all_in_tariff_fingerprint,
            "method": self.method.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AllInTariffAuthority":
        if not isinstance(value, Mapping):
            raise ValueError("all-in tariff authority must be an object")
        if value.get("schema_version") != ALL_IN_TARIFF_AUTHORITY_SCHEMA_VERSION:
            raise ValueError("unsupported all-in tariff authority schema version")
        return cls(
            all_in_tariff_fingerprint=value.get("all_in_tariff_fingerprint"),
            method=value.get("method"),
        )


def all_in_tariff_authorities_from_options(
    options: Mapping[str, Any],
) -> tuple[AllInTariffAuthority, ...]:
    """Load explicit authority records and reject duplicate tariff targets."""

    raw = options.get(OPTION_ALL_IN_TARIFF_AUTHORITIES, [])
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("all_in_tariff_authorities must be a list")

    records: list[AllInTariffAuthority] = []
    seen: set[str] = set()
    for raw_item in raw:
        record = AllInTariffAuthority.from_dict(raw_item)
        if record.all_in_tariff_fingerprint in seen:
            raise ValueError(
                "duplicate all-in tariff authority target: "
                f"{record.all_in_tariff_fingerprint}"
            )
        seen.add(record.all_in_tariff_fingerprint)
        records.append(record)
    return tuple(records)


def all_in_tariff_authority_from_options(
    options: Mapping[str, Any],
    all_in_fingerprint: str,
) -> AllInTariffAuthority:
    """Return the one explicit authority record for a tariff, or fail closed."""

    fingerprint = _fingerprint(all_in_fingerprint)
    record = next(
        (
            item
            for item in all_in_tariff_authorities_from_options(options)
            if item.all_in_tariff_fingerprint == fingerprint
        ),
        None,
    )
    if record is None:
        raise LookupError(f"all-in tariff authority not found: {fingerprint}")
    return record


def append_all_in_tariff_authority(
    options: Mapping[str, Any],
    *,
    all_in_fingerprint: str,
    method: AllInTariffAuthorityMethod | str,
) -> dict[str, Any]:
    """Append one authority label without allowing dangling or mutable records."""

    fingerprint = _fingerprint(all_in_fingerprint)
    authority_method = _method(method)

    all_in_items = all_in_tariffs_from_options(options)
    if not any(all_in_tariff_fingerprint(item) == fingerprint for item in all_in_items):
        raise LookupError(f"all-in tariff target not found: {fingerprint}")

    records = list(all_in_tariff_authorities_from_options(options))
    existing = next(
        (
            item
            for item in records
            if item.all_in_tariff_fingerprint == fingerprint
        ),
        None,
    )
    if existing is not None:
        if existing.method is not authority_method:
            raise ValueError(
                "all-in tariff authority is immutable and cannot change method"
            )
        return dict(options)

    records.append(
        AllInTariffAuthority(
            all_in_tariff_fingerprint=fingerprint,
            method=authority_method,
        )
    )
    updated = dict(options)
    updated[OPTION_ALL_IN_TARIFF_AUTHORITIES] = [item.as_dict() for item in records]
    return updated
