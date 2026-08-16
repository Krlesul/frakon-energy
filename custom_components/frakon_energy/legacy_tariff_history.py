"""Immutable historical snapshots for pre-catalog flat all-in tariff options.

Legacy FRAKON Energy versions stored only three already-all-in billing values:
VT, NT and a fixed monthly total.  Those values do not contain enough evidence to
reconstruct supplier-commercial vs. regulated components, so this module keeps
them in a deliberately separate historical store instead of fabricating an
``AllInTariffAssembly`` or official provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, Mapping

LEGACY_TARIFF_HISTORY_SCHEMA_VERSION = 1
OPTION_LEGACY_TARIFF_HISTORY = "legacy_tariff_history"
LEGACY_PRICE_VT_OPTION = "price_vt_czk_kwh"
LEGACY_PRICE_NT_OPTION = "price_nt_czk_kwh"
LEGACY_FIXED_MONTHLY_OPTION = "fixed_monthly_czk"
LEGACY_PRICE_OPTION_KEYS = (
    LEGACY_PRICE_VT_OPTION,
    LEGACY_PRICE_NT_OPTION,
    LEGACY_FIXED_MONTHLY_OPTION,
)
LEGACY_TARIFF_SOURCE = "legacy_options"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class LegacyTariffAuthorityMethod(StrEnum):
    """Authority label for explicitly confirmed historical legacy values."""

    LEGACY_MANUAL_IMPORT = "legacy_manual_import"


def _date_value(value: Any, field: str) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value)
        except ValueError as err:
            raise ValueError(f"{field} must be an ISO-8601 date") from err
    raise ValueError(f"{field} must be an ISO-8601 date")


def _decimal_value(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        parsed = Decimal(str(value))
    except Exception as err:
        raise ValueError(f"{field} must be numeric") from err
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return parsed


def _decimal_string(value: Decimal) -> str:
    return format(_decimal_value(value, "price value"), "f")


def _fingerprint(value: Any) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError("fingerprint must be a lowercase SHA-256 digest")
    return value


def _windows_overlap(
    first_from: date,
    first_to: date,
    second_from: date,
    second_to: date,
) -> bool:
    return first_from <= second_to and second_from <= first_to


@dataclass(frozen=True, slots=True)
class LegacyTariffSnapshot:
    """One bounded historical all-in price copied from old billing options."""

    valid_from: date
    valid_to: date
    high_rate_czk_per_kwh: Decimal
    low_rate_czk_per_kwh: Decimal
    fixed_monthly_czk: Decimal
    confirmed: bool = False
    source: str = LEGACY_TARIFF_SOURCE
    authority_method: LegacyTariffAuthorityMethod = (
        LegacyTariffAuthorityMethod.LEGACY_MANUAL_IMPORT
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "valid_from", _date_value(self.valid_from, "valid_from"))
        object.__setattr__(self, "valid_to", _date_value(self.valid_to, "valid_to"))
        if self.valid_to < self.valid_from:
            raise ValueError("valid_to must not precede valid_from")
        object.__setattr__(
            self,
            "high_rate_czk_per_kwh",
            _decimal_value(self.high_rate_czk_per_kwh, "high_rate_czk_per_kwh"),
        )
        object.__setattr__(
            self,
            "low_rate_czk_per_kwh",
            _decimal_value(self.low_rate_czk_per_kwh, "low_rate_czk_per_kwh"),
        )
        object.__setattr__(
            self,
            "fixed_monthly_czk",
            _decimal_value(self.fixed_monthly_czk, "fixed_monthly_czk"),
        )
        if not isinstance(self.confirmed, bool):
            raise ValueError("confirmed must be boolean")
        if self.source != LEGACY_TARIFF_SOURCE:
            raise ValueError("legacy tariff source must remain legacy_options")
        if self.authority_method is not LegacyTariffAuthorityMethod.LEGACY_MANUAL_IMPORT:
            raise ValueError("legacy tariff authority must remain legacy_manual_import")

    @property
    def component_breakdown_available(self) -> bool:
        return False

    @property
    def official_provenance_available(self) -> bool:
        return False

    def applies_on(self, day: date) -> bool:
        if not isinstance(day, date):
            raise ValueError("day must be a date")
        return self.valid_from <= day <= self.valid_to

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LEGACY_TARIFF_HISTORY_SCHEMA_VERSION,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat(),
            "high_rate_czk_per_kwh": _decimal_string(self.high_rate_czk_per_kwh),
            "low_rate_czk_per_kwh": _decimal_string(self.low_rate_czk_per_kwh),
            "fixed_monthly_czk": _decimal_string(self.fixed_monthly_czk),
            "confirmed": self.confirmed,
            "source": self.source,
            "authority_method": self.authority_method.value,
            "component_breakdown_available": False,
            "official_provenance_available": False,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LegacyTariffSnapshot":
        if not isinstance(value, Mapping):
            raise ValueError("legacy tariff snapshot must be an object")
        if value.get("schema_version") != LEGACY_TARIFF_HISTORY_SCHEMA_VERSION:
            raise ValueError("unsupported legacy tariff history schema version")
        if value.get("component_breakdown_available") is not False:
            raise ValueError("legacy tariff cannot claim a component breakdown")
        if value.get("official_provenance_available") is not False:
            raise ValueError("legacy tariff cannot claim official provenance")
        try:
            authority = LegacyTariffAuthorityMethod(value.get("authority_method"))
        except (TypeError, ValueError) as err:
            raise ValueError("unsupported legacy tariff authority method") from err
        return cls(
            valid_from=_date_value(value.get("valid_from"), "valid_from"),
            valid_to=_date_value(value.get("valid_to"), "valid_to"),
            high_rate_czk_per_kwh=_decimal_value(
                value.get("high_rate_czk_per_kwh"),
                "high_rate_czk_per_kwh",
            ),
            low_rate_czk_per_kwh=_decimal_value(
                value.get("low_rate_czk_per_kwh"),
                "low_rate_czk_per_kwh",
            ),
            fixed_monthly_czk=_decimal_value(
                value.get("fixed_monthly_czk"),
                "fixed_monthly_czk",
            ),
            confirmed=value.get("confirmed", False),
            source=value.get("source"),
            authority_method=authority,
        )


def legacy_tariff_fingerprint(snapshot: LegacyTariffSnapshot) -> str:
    """Stable identity that intentionally excludes confirmation state."""

    if not isinstance(snapshot, LegacyTariffSnapshot):
        raise ValueError("snapshot must be LegacyTariffSnapshot")
    payload = snapshot.as_dict()
    payload["confirmed"] = False
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def legacy_price_values_from_options(
    options: Mapping[str, Any],
) -> tuple[Decimal, Decimal, Decimal]:
    """Read the complete old flat-price triple or fail closed on partial state."""

    if not isinstance(options, Mapping):
        raise ValueError("options must be a mapping")
    present = tuple(key in options for key in LEGACY_PRICE_OPTION_KEYS)
    if not any(present):
        raise LookupError("legacy billing price options are not available")
    if not all(present):
        raise ValueError("legacy billing price options are incomplete")
    return (
        _decimal_value(options[LEGACY_PRICE_VT_OPTION], LEGACY_PRICE_VT_OPTION),
        _decimal_value(options[LEGACY_PRICE_NT_OPTION], LEGACY_PRICE_NT_OPTION),
        _decimal_value(options[LEGACY_FIXED_MONTHLY_OPTION], LEGACY_FIXED_MONTHLY_OPTION),
    )


def legacy_tariff_snapshot_from_options(
    options: Mapping[str, Any],
    *,
    valid_from: date,
    valid_to: date,
) -> LegacyTariffSnapshot:
    """Build an unconfirmed historical snapshot from server-side legacy options."""

    high, low, fixed = legacy_price_values_from_options(options)
    return LegacyTariffSnapshot(
        valid_from=valid_from,
        valid_to=valid_to,
        high_rate_czk_per_kwh=high,
        low_rate_czk_per_kwh=low,
        fixed_monthly_czk=fixed,
        confirmed=False,
    )


def legacy_tariff_history_from_options(
    options: Mapping[str, Any],
) -> tuple[LegacyTariffSnapshot, ...]:
    raw = options.get(OPTION_LEGACY_TARIFF_HISTORY, [])
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("legacy_tariff_history must be a list")
    snapshots: list[LegacyTariffSnapshot] = []
    seen: set[str] = set()
    for raw_item in raw:
        snapshot = LegacyTariffSnapshot.from_dict(raw_item)
        fingerprint = legacy_tariff_fingerprint(snapshot)
        if fingerprint in seen:
            raise ValueError(f"duplicate legacy tariff fingerprint: {fingerprint}")
        seen.add(fingerprint)
        snapshots.append(snapshot)
    return tuple(snapshots)


def append_legacy_tariff_snapshot(
    options: Mapping[str, Any],
    snapshot: LegacyTariffSnapshot,
) -> dict[str, Any]:
    """Stage one immutable unconfirmed historical snapshot idempotently."""

    if not isinstance(snapshot, LegacyTariffSnapshot):
        raise ValueError("snapshot must be LegacyTariffSnapshot")
    if snapshot.confirmed:
        raise ValueError("new legacy tariff snapshot must be unconfirmed")
    fingerprint = legacy_tariff_fingerprint(snapshot)
    snapshots = list(legacy_tariff_history_from_options(options))
    if any(legacy_tariff_fingerprint(item) == fingerprint for item in snapshots):
        return dict(options)
    snapshots.append(snapshot)
    updated = dict(options)
    updated[OPTION_LEGACY_TARIFF_HISTORY] = [item.as_dict() for item in snapshots]
    return updated


def confirm_legacy_tariff_snapshot(
    options: Mapping[str, Any],
    fingerprint: str,
) -> dict[str, Any]:
    """Confirm one staged snapshot while rejecting overlapping confirmed history."""

    target = _fingerprint(fingerprint)
    snapshots = list(legacy_tariff_history_from_options(options))
    matched_index: int | None = None
    for index, snapshot in enumerate(snapshots):
        if legacy_tariff_fingerprint(snapshot) == target:
            matched_index = index
            break
    if matched_index is None:
        raise LookupError(f"legacy tariff snapshot not found: {target}")

    candidate = snapshots[matched_index]
    if candidate.confirmed:
        return dict(options)
    for index, existing in enumerate(snapshots):
        if index == matched_index or not existing.confirmed:
            continue
        if _windows_overlap(
            candidate.valid_from,
            candidate.valid_to,
            existing.valid_from,
            existing.valid_to,
        ):
            raise ValueError("legacy tariff snapshot overlaps confirmed legacy history")

    snapshots[matched_index] = replace(candidate, confirmed=True)
    updated = dict(options)
    updated[OPTION_LEGACY_TARIFF_HISTORY] = [item.as_dict() for item in snapshots]
    return updated


def select_confirmed_legacy_tariff_for_day(
    snapshots: tuple[LegacyTariffSnapshot, ...],
    day: date,
) -> LegacyTariffSnapshot:
    """Return exactly one confirmed historical legacy snapshot for ``day``."""

    if not isinstance(day, date):
        raise ValueError("day must be a date")
    matches = [item for item in snapshots if item.confirmed and item.applies_on(day)]
    if not matches:
        raise LookupError(f"No confirmed legacy tariff applies on {day.isoformat()}")
    if len(matches) != 1:
        raise ValueError(f"ambiguous confirmed legacy tariffs for {day.isoformat()}")
    return matches[0]


def confirmed_legacy_tariff_from_options(
    options: Mapping[str, Any],
    day: date,
) -> LegacyTariffSnapshot:
    return select_confirmed_legacy_tariff_for_day(
        legacy_tariff_history_from_options(options),
        day,
    )
