"""Durable all-in tariff catalog with multi-source provenance."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import hashlib
import json
from typing import Any, Mapping
import unicodedata

from .pricing import FixedPriceComponent, VariablePriceComponent
from .tariff_assembly import AllInTariffAssembly
from .tariff_provenance import MultiSourceTariffProvenance

ALL_IN_CATALOG_SCHEMA_VERSION = 1
OPTION_ALL_IN_TARIFF_CATALOG = "all_in_tariff_catalog"


def _date_from_value(value: Any, field: str) -> date:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 date")
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"{field} must be an ISO-8601 date") from err


def _supplier_identity(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("supplier must not be empty")
    decomposed = unicodedata.normalize("NFKD", value.strip()).casefold()
    normalized = "".join(char for char in decomposed if char.isalnum())
    if not normalized:
        raise ValueError("supplier must contain an alphanumeric identity")
    return normalized


@dataclass(frozen=True, slots=True)
class PersistedAllInTariff:
    """One immutable all-in assembly version plus explicit activation state."""

    assembly: AllInTariffAssembly
    confirmed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.assembly, AllInTariffAssembly):
            raise ValueError("assembly must be AllInTariffAssembly")
        if not self.assembly.all_in_ready:
            raise ValueError("assembly must be all-in ready")
        if not isinstance(self.confirmed, bool):
            raise ValueError("confirmed must be boolean")

    def applies_on(self, day: date) -> bool:
        if not isinstance(day, date):
            raise ValueError("day must be a date")
        return self.assembly.valid_from <= day and (
            self.assembly.valid_to is None or day <= self.assembly.valid_to
        )

    def as_dict(self) -> dict[str, Any]:
        assembly = self.assembly
        return {
            "schema_version": ALL_IN_CATALOG_SCHEMA_VERSION,
            "confirmed": self.confirmed,
            "assembly": {
                "supplier": assembly.supplier,
                "product_name": assembly.product_name,
                "distribution_tariff": assembly.distribution_tariff,
                "breaker_code": assembly.breaker_code,
                "valid_from": assembly.valid_from.isoformat(),
                "valid_to": assembly.valid_to.isoformat() if assembly.valid_to is not None else None,
                "variable_components": [item.as_dict() for item in assembly.variable_components],
                "fixed_components": [item.as_dict() for item in assembly.fixed_components],
                "provenance": assembly.provenance.as_dict(),
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PersistedAllInTariff:
        if not isinstance(value, Mapping):
            raise ValueError("all-in tariff catalog item must be an object")
        if value.get("schema_version") != ALL_IN_CATALOG_SCHEMA_VERSION:
            raise ValueError("unsupported all-in tariff catalog schema version")
        raw = value.get("assembly")
        if not isinstance(raw, Mapping):
            raise ValueError("all-in tariff assembly must be an object")
        variable_raw = raw.get("variable_components")
        fixed_raw = raw.get("fixed_components")
        provenance_raw = raw.get("provenance")
        if not isinstance(variable_raw, list) or not isinstance(fixed_raw, list):
            raise ValueError("all-in tariff components must be lists")
        if not isinstance(provenance_raw, Mapping):
            raise ValueError("all-in tariff provenance must be an object")
        valid_to_raw = raw.get("valid_to")
        assembly = AllInTariffAssembly(
            supplier=raw.get("supplier"),
            product_name=raw.get("product_name"),
            distribution_tariff=raw.get("distribution_tariff"),
            breaker_code=raw.get("breaker_code"),
            valid_from=_date_from_value(raw.get("valid_from"), "valid_from"),
            valid_to=(
                _date_from_value(valid_to_raw, "valid_to")
                if valid_to_raw not in (None, "")
                else None
            ),
            variable_components=tuple(
                VariablePriceComponent.from_dict(item) for item in variable_raw
            ),
            fixed_components=tuple(
                FixedPriceComponent.from_dict(item) for item in fixed_raw
            ),
            provenance=MultiSourceTariffProvenance.from_dict(provenance_raw),
        )
        return cls(assembly=assembly, confirmed=value.get("confirmed", False))


def all_in_tariff_fingerprint(item: PersistedAllInTariff) -> str:
    """Stable identity that intentionally ignores confirmation state."""

    if not isinstance(item, PersistedAllInTariff):
        raise ValueError("item must be PersistedAllInTariff")
    payload = item.as_dict()
    payload["confirmed"] = False
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def all_in_tariffs_from_options(
    options: Mapping[str, Any],
) -> tuple[PersistedAllInTariff, ...]:
    raw = options.get(OPTION_ALL_IN_TARIFF_CATALOG, [])
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("all_in_tariff_catalog must be a list")

    items: list[PersistedAllInTariff] = []
    seen: set[str] = set()
    for raw_item in raw:
        if not isinstance(raw_item, Mapping):
            raise ValueError("each all-in tariff catalog item must be an object")
        item = PersistedAllInTariff.from_dict(raw_item)
        fingerprint = all_in_tariff_fingerprint(item)
        if fingerprint in seen:
            raise ValueError(f"duplicate all-in tariff fingerprint: {fingerprint}")
        seen.add(fingerprint)
        items.append(item)
    return tuple(items)


def append_all_in_tariff(
    options: Mapping[str, Any], assembly: AllInTariffAssembly
) -> dict[str, Any]:
    """Append an unconfirmed immutable all-in version without overwriting history."""

    candidate = PersistedAllInTariff(assembly=assembly, confirmed=False)
    fingerprint = all_in_tariff_fingerprint(candidate)
    items = list(all_in_tariffs_from_options(options))
    if any(all_in_tariff_fingerprint(item) == fingerprint for item in items):
        return dict(options)
    items.append(candidate)
    updated = dict(options)
    updated[OPTION_ALL_IN_TARIFF_CATALOG] = [item.as_dict() for item in items]
    return updated


def confirm_all_in_tariff(options: Mapping[str, Any], fingerprint: str) -> dict[str, Any]:
    """Confirm exactly one stored all-in version without changing its identity."""

    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(char not in "0123456789abcdef" for char in fingerprint)
    ):
        raise ValueError("fingerprint must be a lowercase SHA-256 hex digest")

    items = list(all_in_tariffs_from_options(options))
    matched = False
    for index, item in enumerate(items):
        if all_in_tariff_fingerprint(item) != fingerprint:
            continue
        matched = True
        if not item.confirmed:
            items[index] = replace(item, confirmed=True)
        break
    if not matched:
        raise LookupError(f"all-in tariff not found: {fingerprint}")

    updated = dict(options)
    updated[OPTION_ALL_IN_TARIFF_CATALOG] = [item.as_dict() for item in items]
    return updated


def _select_unique_newest_all_in(
    matches: list[PersistedAllInTariff],
    day: date,
) -> PersistedAllInTariff:
    if not matches:
        raise LookupError(f"No confirmed all-in tariff applies on {day.isoformat()}")
    newest_valid_from = max(item.assembly.valid_from for item in matches)
    newest = [item for item in matches if item.assembly.valid_from == newest_valid_from]
    if len(newest) != 1:
        raise ValueError(f"ambiguous confirmed all-in tariffs for {day.isoformat()}")
    return newest[0]


def select_confirmed_all_in_tariff(
    items: tuple[PersistedAllInTariff, ...], day: date
) -> PersistedAllInTariff:
    matches = [item for item in items if item.confirmed and item.applies_on(day)]
    return _select_unique_newest_all_in(matches, day)


def select_confirmed_all_in_tariff_for_context(
    items: tuple[PersistedAllInTariff, ...],
    *,
    supplier: str,
    product_name: str,
    distribution_tariff: str,
    breaker_code: str,
    day: date,
) -> PersistedAllInTariff:
    """Select one exact confirmed customer all-in version or fail closed."""
    if not isinstance(day, date):
        raise ValueError("day must be a date")
    if not isinstance(product_name, str) or not product_name.strip():
        raise ValueError("product_name must not be empty")
    if not isinstance(distribution_tariff, str) or not distribution_tariff.strip():
        raise ValueError("distribution_tariff must not be empty")
    if not isinstance(breaker_code, str) or not breaker_code.strip():
        raise ValueError("breaker_code must not be empty")
    supplier_key = _supplier_identity(supplier)
    matches = [
        item
        for item in items
        if item.confirmed
        and item.applies_on(day)
        and _supplier_identity(item.assembly.supplier) == supplier_key
        and item.assembly.product_name.strip() == product_name.strip()
        and item.assembly.distribution_tariff == distribution_tariff.strip()
        and item.assembly.breaker_code == breaker_code.strip()
    ]
    return _select_unique_newest_all_in(matches, day)


def confirmed_all_in_tariff_from_options(
    options: Mapping[str, Any], day: date
) -> PersistedAllInTariff:
    return select_confirmed_all_in_tariff(all_in_tariffs_from_options(options), day)


def confirmed_all_in_tariff_for_context_from_options(
    options: Mapping[str, Any],
    *,
    supplier: str,
    product_name: str,
    distribution_tariff: str,
    breaker_code: str,
    day: date,
) -> PersistedAllInTariff:
    return select_confirmed_all_in_tariff_for_context(
        all_in_tariffs_from_options(options),
        supplier=supplier,
        product_name=product_name,
        distribution_tariff=distribution_tariff,
        breaker_code=breaker_code,
        day=day,
    )
