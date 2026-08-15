"""Immutable confirmed-source fallback for postcode-gated MND tariff documents.

MND currently does not expose a stable public supply-document API that FRAKON can
safely reproduce. This module provides a manual-but-cryptographically-pinned
fallback: an administrator can confirm one exact official MND document after it
has been independently downloaded and hashed. Only the operational source-context
fingerprint is stored; the raw postcode is never persisted here.

The resolver is intentionally read-only. It returns a source only for one exact
context/product/distributor/contract-kind/day match. Overlapping confirmed records
fail closed instead of choosing a winner implicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import re
from typing import Any, Callable, Mapping

from .mnd_tariffs import (
    MndProductDefinition,
    MndResolvedTariffSource,
    mnd_product_definition,
)
from ..tariff_sources import TariffSourceQuery, tariff_source_context_fingerprint

MND_CONFIRMED_SOURCE_RESOLUTIONS_OPTION = "confirmed_mnd_source_resolutions"
MND_CONFIRMED_SOURCE_RESOLUTION_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _non_empty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value.strip()


def _sha256(value: Any, field: str) -> str:
    digest = _non_empty(value, field).lower()
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return digest


def _parse_date(value: Any, field: str, *, optional: bool = False) -> date | None:
    if optional and value in (None, ""):
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 date")
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"{field} must be an ISO-8601 date") from err


def _parse_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 datetime")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"{field} must be an ISO-8601 datetime") from err
    if result.tzinfo is None:
        raise ValueError(f"{field} must include timezone information")
    return result


def _catalog_product(product_name: str, contract_kind: str) -> MndProductDefinition:
    product = mnd_product_definition(product_name, contract_kind)
    if product is None:
        raise ValueError("confirmed MND source must match exactly one verified product")
    return product


@dataclass(frozen=True, slots=True)
class ConfirmedMndSourceResolution:
    """One immutable exact MND document bound to hashed operational context."""

    source_context_fingerprint: str
    product_name: str
    distributor: str
    contract_kind: str
    source_url: str
    valid_from: date
    valid_to: date | None
    document_date: date | None
    document_sha256: str
    confirmed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_context_fingerprint",
            _sha256(self.source_context_fingerprint, "source_context_fingerprint"),
        )
        product_name = _non_empty(self.product_name, "product_name")
        distributor = _non_empty(self.distributor, "distributor")
        contract_kind = _non_empty(self.contract_kind, "contract_kind")
        product = _catalog_product(product_name, contract_kind)
        object.__setattr__(self, "product_name", product.product_name)
        object.__setattr__(self, "distributor", distributor)
        object.__setattr__(self, "contract_kind", contract_kind)
        object.__setattr__(self, "source_url", _non_empty(self.source_url, "source_url"))
        if not isinstance(self.valid_from, date):
            raise ValueError("valid_from must be a date")
        if self.valid_to is not None:
            if not isinstance(self.valid_to, date):
                raise ValueError("valid_to must be a date")
            if self.valid_to < self.valid_from:
                raise ValueError("valid_to must not precede valid_from")
        if product.advertised_valid_to is not None and self.valid_to != product.advertised_valid_to:
            raise ValueError(
                "confirmed MND source validity end does not match public product evidence"
            )
        if self.document_date is not None and not isinstance(self.document_date, date):
            raise ValueError("document_date must be a date")
        object.__setattr__(
            self,
            "document_sha256",
            _sha256(self.document_sha256, "document_sha256"),
        )
        if not isinstance(self.confirmed_at, datetime) or self.confirmed_at.tzinfo is None:
            raise ValueError("confirmed_at must be a timezone-aware datetime")

        MndResolvedTariffSource(
            product_name=self.product_name,
            distributor=self.distributor,
            contract_kind=self.contract_kind,
            source_url=self.source_url,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
            document_date=self.document_date,
            discovered_at=self.confirmed_at,
            sha256=self.document_sha256,
        )

    def applies_on(self, day: date) -> bool:
        if not isinstance(day, date):
            raise ValueError("day must be a date")
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MND_CONFIRMED_SOURCE_RESOLUTION_SCHEMA_VERSION,
            "source_context_fingerprint": self.source_context_fingerprint,
            "product_name": self.product_name,
            "distributor": self.distributor,
            "contract_kind": self.contract_kind,
            "source_url": self.source_url,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to is not None else None,
            "document_date": (
                self.document_date.isoformat() if self.document_date is not None else None
            ),
            "document_sha256": self.document_sha256,
            "confirmed_at": self.confirmed_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ConfirmedMndSourceResolution:
        if not isinstance(value, Mapping):
            raise ValueError("confirmed MND source resolution must be an object")
        if value.get("schema_version") != MND_CONFIRMED_SOURCE_RESOLUTION_SCHEMA_VERSION:
            raise ValueError("unsupported confirmed MND source resolution schema version")
        valid_from = _parse_date(value.get("valid_from"), "valid_from")
        if valid_from is None:
            raise ValueError("valid_from must be an ISO-8601 date")
        return cls(
            source_context_fingerprint=value.get("source_context_fingerprint"),
            product_name=value.get("product_name"),
            distributor=value.get("distributor"),
            contract_kind=value.get("contract_kind"),
            source_url=value.get("source_url"),
            valid_from=valid_from,
            valid_to=_parse_date(value.get("valid_to"), "valid_to", optional=True),
            document_date=_parse_date(
                value.get("document_date"), "document_date", optional=True
            ),
            document_sha256=value.get("document_sha256"),
            confirmed_at=_parse_datetime(value.get("confirmed_at"), "confirmed_at"),
        )


def confirmed_mnd_source_resolution_fingerprint(
    resolution: ConfirmedMndSourceResolution,
) -> str:
    """Stable immutable content identity excluding confirmation timestamp."""
    if not isinstance(resolution, ConfirmedMndSourceResolution):
        raise ValueError("resolution must be ConfirmedMndSourceResolution")
    payload = {
        "source_context_fingerprint": resolution.source_context_fingerprint,
        "product_name": resolution.product_name,
        "distributor": resolution.distributor,
        "contract_kind": resolution.contract_kind,
        "source_url": resolution.source_url,
        "valid_from": resolution.valid_from.isoformat(),
        "valid_to": resolution.valid_to.isoformat() if resolution.valid_to is not None else None,
        "document_date": (
            resolution.document_date.isoformat()
            if resolution.document_date is not None
            else None
        ),
        "document_sha256": resolution.document_sha256,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def confirmed_mnd_source_resolutions_from_options(
    options: Mapping[str, Any],
) -> tuple[ConfirmedMndSourceResolution, ...]:
    if not isinstance(options, Mapping):
        raise ValueError("options must be a mapping")
    raw = options.get(MND_CONFIRMED_SOURCE_RESOLUTIONS_OPTION, ())
    if raw in (None, ""):
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ValueError("confirmed MND source resolutions option must be a list")
    return tuple(ConfirmedMndSourceResolution.from_dict(item) for item in raw)


def append_confirmed_mnd_source_resolution(
    options: Mapping[str, Any],
    resolution: ConfirmedMndSourceResolution,
) -> dict[str, Any]:
    """Append immutably; exact repeats are idempotent and never overwrite history."""
    if not isinstance(options, Mapping):
        raise ValueError("options must be a mapping")
    if not isinstance(resolution, ConfirmedMndSourceResolution):
        raise ValueError("resolution must be ConfirmedMndSourceResolution")
    existing = confirmed_mnd_source_resolutions_from_options(options)
    fingerprint = confirmed_mnd_source_resolution_fingerprint(resolution)
    if any(
        confirmed_mnd_source_resolution_fingerprint(item) == fingerprint
        for item in existing
    ):
        return dict(options)
    updated = dict(options)
    updated[MND_CONFIRMED_SOURCE_RESOLUTIONS_OPTION] = [
        item.as_dict() for item in (*existing, resolution)
    ]
    return updated


class MndConfirmedSourceResolver:
    """Resolve only exact, previously confirmed and SHA-pinned MND sources."""

    def __init__(
        self,
        resolutions: tuple[ConfirmedMndSourceResolution, ...],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        values = tuple(resolutions)
        if any(not isinstance(item, ConfirmedMndSourceResolution) for item in values):
            raise ValueError("resolutions must contain ConfirmedMndSourceResolution items")
        self._resolutions = values
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def async_resolve(
        self,
        query: TariffSourceQuery,
        product: MndProductDefinition,
    ) -> MndResolvedTariffSource | None:
        if not isinstance(query, TariffSourceQuery):
            raise ValueError("query must be TariffSourceQuery")
        if not isinstance(product, MndProductDefinition):
            raise ValueError("product must be MndProductDefinition")
        context_fingerprint = tariff_source_context_fingerprint(query.source_context)
        matches = tuple(
            item
            for item in self._resolutions
            if item.source_context_fingerprint == context_fingerprint
            and item.product_name == product.product_name
            and item.distributor == query.distributor
            and item.contract_kind == product.contract_kind
            and item.applies_on(query.valid_on)
        )
        if not matches:
            return None
        if len(matches) != 1:
            raise ValueError("ambiguous confirmed MND source resolution for query")
        selected = matches[0]
        discovered_at = self._clock()
        if not isinstance(discovered_at, datetime) or discovered_at.tzinfo is None:
            raise ValueError("resolver clock must return a timezone-aware datetime")
        return MndResolvedTariffSource(
            product_name=selected.product_name,
            distributor=selected.distributor,
            contract_kind=selected.contract_kind,
            source_url=selected.source_url,
            valid_from=selected.valid_from,
            valid_to=selected.valid_to,
            document_date=selected.document_date,
            discovered_at=discovered_at,
            sha256=selected.document_sha256,
        )


def mnd_confirmed_source_resolver_from_options(
    options: Mapping[str, Any],
    *,
    clock: Callable[[], datetime] | None = None,
) -> MndConfirmedSourceResolver:
    return MndConfirmedSourceResolver(
        confirmed_mnd_source_resolutions_from_options(options),
        clock=clock,
    )
