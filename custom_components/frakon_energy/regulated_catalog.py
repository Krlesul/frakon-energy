"""Immutable storage for already-confirmed regulated tariff versions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import hashlib
import json
from typing import Any, Mapping

from .pricing import FixedPriceComponent, PriceComponentKind, VariablePriceComponent
from .regulated_pricing import RegulatedTariffBundle
from .tariff_provenance import PriceEvidence, evidence_fingerprint
from .tariff_sources import PRICE_SCOPE_REGULATED

REGULATED_CATALOG_SCHEMA_VERSION = 1
OPTION_CONFIRMED_REGULATED_TARIFFS = "confirmed_regulated_tariffs"


def _decimal_text(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("regulated catalog prices must be finite non-negative Decimals")
    return format(value, "f")


def _date_from_value(value: Any, field: str) -> date:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 date")
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"{field} must be an ISO-8601 date") from err


def _optional_date(value: Any, field: str) -> date | None:
    if value in (None, ""):
        return None
    return _date_from_value(value, field)


def _variable_to_dict(item: VariablePriceComponent) -> dict[str, Any]:
    if not isinstance(item, VariablePriceComponent):
        raise ValueError("regulated variable component must be VariablePriceComponent")
    return {
        "kind": item.kind.value,
        "name": item.name,
        "high_rate_czk_per_kwh": _decimal_text(item.high_rate_czk_per_kwh),
        "low_rate_czk_per_kwh": _decimal_text(item.low_rate_czk_per_kwh),
        "includes_vat": item.includes_vat,
    }


def _fixed_to_dict(item: FixedPriceComponent) -> dict[str, Any]:
    if not isinstance(item, FixedPriceComponent):
        raise ValueError("regulated fixed component must be FixedPriceComponent")
    return {
        "kind": item.kind.value,
        "name": item.name,
        "monthly_czk": _decimal_text(item.monthly_czk),
        "includes_vat": item.includes_vat,
    }


def _variable_from_dict(value: Mapping[str, Any]) -> VariablePriceComponent:
    if not isinstance(value, Mapping):
        raise ValueError("regulated variable component must be an object")
    try:
        kind = PriceComponentKind(str(value["kind"]))
        high_rate = Decimal(str(value["high_rate_czk_per_kwh"]))
        low_rate = Decimal(str(value["low_rate_czk_per_kwh"]))
    except (KeyError, ValueError) as err:
        raise ValueError("invalid regulated variable component") from err
    return VariablePriceComponent(
        kind=kind,
        name=value.get("name"),
        high_rate_czk_per_kwh=high_rate,
        low_rate_czk_per_kwh=low_rate,
        includes_vat=value.get("includes_vat"),
    )


def _fixed_from_dict(value: Mapping[str, Any]) -> FixedPriceComponent:
    if not isinstance(value, Mapping):
        raise ValueError("regulated fixed component must be an object")
    try:
        kind = PriceComponentKind(str(value["kind"]))
        monthly = Decimal(str(value["monthly_czk"]))
    except (KeyError, ValueError) as err:
        raise ValueError("invalid regulated fixed component") from err
    return FixedPriceComponent(
        kind=kind,
        name=value.get("name"),
        monthly_czk=monthly,
        includes_vat=value.get("includes_vat"),
    )


def _bundle_to_dict(bundle: RegulatedTariffBundle) -> dict[str, Any]:
    if not isinstance(bundle, RegulatedTariffBundle):
        raise ValueError("bundle must be RegulatedTariffBundle")
    return {
        "distributor": bundle.distributor,
        "distribution_tariff": bundle.distribution_tariff,
        "breaker_code": bundle.breaker_code,
        "valid_from": bundle.valid_from.isoformat(),
        "valid_to": bundle.valid_to.isoformat() if bundle.valid_to else None,
        "variable_components": [_variable_to_dict(item) for item in bundle.variable_components],
        "fixed_components": [_fixed_to_dict(item) for item in bundle.fixed_components],
        "source_url": bundle.source_url,
        "document_date": bundle.document_date.isoformat() if bundle.document_date else None,
        "checksum": bundle.checksum,
        "confirmed": bundle.confirmed,
    }


def _bundle_from_dict(value: Mapping[str, Any]) -> RegulatedTariffBundle:
    if not isinstance(value, Mapping):
        raise ValueError("regulated bundle must be an object")
    raw_variable = value.get("variable_components")
    raw_fixed = value.get("fixed_components")
    if not isinstance(raw_variable, list) or not isinstance(raw_fixed, list):
        raise ValueError("regulated component collections must be lists")
    return RegulatedTariffBundle(
        distributor=value.get("distributor"),
        distribution_tariff=value.get("distribution_tariff"),
        breaker_code=value.get("breaker_code"),
        valid_from=_date_from_value(value.get("valid_from"), "valid_from"),
        valid_to=_optional_date(value.get("valid_to"), "valid_to"),
        variable_components=tuple(_variable_from_dict(item) for item in raw_variable),
        fixed_components=tuple(_fixed_from_dict(item) for item in raw_fixed),
        source_url=value.get("source_url"),
        document_date=_optional_date(value.get("document_date"), "document_date"),
        checksum=value.get("checksum"),
        confirmed=value.get("confirmed"),
    )


def _validate_evidence(bundle: RegulatedTariffBundle, evidence: tuple[PriceEvidence, ...]) -> None:
    if not evidence:
        raise ValueError("confirmed regulated tariff requires evidence")
    if any(not isinstance(item, PriceEvidence) for item in evidence):
        raise ValueError("regulated evidence must contain PriceEvidence records")
    if any(item.scope != PRICE_SCOPE_REGULATED for item in evidence):
        raise ValueError("regulated catalog evidence must be regulated-only")
    matching_source = tuple(item for item in evidence if item.source_url == bundle.source_url)
    if not matching_source:
        raise ValueError("regulated evidence does not contain bundle source URL")
    if bundle.checksum is not None and not any(
        item.checksum == bundle.checksum for item in matching_source
    ):
        raise ValueError("regulated evidence checksum does not match bundle checksum")


def regulated_version_fingerprint(
    bundle: RegulatedTariffBundle,
    evidence: tuple[PriceEvidence, ...],
) -> str:
    """Return stable identity for one complete confirmed regulated price version."""
    if not isinstance(bundle, RegulatedTariffBundle):
        raise ValueError("bundle must be RegulatedTariffBundle")
    evidence = tuple(evidence)
    _validate_evidence(bundle, evidence)
    payload = {
        "bundle": _bundle_to_dict(bundle),
        "evidence": [
            item.as_dict()
            for item in sorted(evidence, key=evidence_fingerprint)
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ConfirmedRegulatedTariffVersion:
    """One immutable confirmed regulator version and the evidence authorizing it."""

    bundle: RegulatedTariffBundle
    evidence: tuple[PriceEvidence, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.bundle, RegulatedTariffBundle):
            raise ValueError("bundle must be RegulatedTariffBundle")
        if self.bundle.confirmed is not True:
            raise ValueError("regulated catalog stores confirmed bundles only")
        evidence = tuple(self.evidence)
        _validate_evidence(self.bundle, evidence)
        object.__setattr__(self, "evidence", evidence)

    @property
    def fingerprint(self) -> str:
        return regulated_version_fingerprint(self.bundle, self.evidence)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REGULATED_CATALOG_SCHEMA_VERSION,
            "fingerprint": self.fingerprint,
            "bundle": _bundle_to_dict(self.bundle),
            "evidence": [item.as_dict() for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ConfirmedRegulatedTariffVersion:
        if not isinstance(value, Mapping):
            raise ValueError("confirmed regulated tariff version must be an object")
        if value.get("schema_version") != REGULATED_CATALOG_SCHEMA_VERSION:
            raise ValueError("unsupported regulated catalog schema version")
        raw_evidence = value.get("evidence")
        if not isinstance(raw_evidence, list):
            raise ValueError("regulated evidence must be a list")
        version = cls(
            bundle=_bundle_from_dict(value.get("bundle")),
            evidence=tuple(PriceEvidence.from_dict(item) for item in raw_evidence),
        )
        if value.get("fingerprint") != version.fingerprint:
            raise ValueError("regulated catalog fingerprint mismatch")
        return version


def confirmed_regulated_versions_from_options(
    options: Mapping[str, Any],
) -> tuple[ConfirmedRegulatedTariffVersion, ...]:
    raw = options.get(OPTION_CONFIRMED_REGULATED_TARIFFS, [])
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("confirmed_regulated_tariffs must be a list")
    versions: list[ConfirmedRegulatedTariffVersion] = []
    seen: set[str] = set()
    for item in raw:
        version = ConfirmedRegulatedTariffVersion.from_dict(item)
        if version.fingerprint in seen:
            raise ValueError(f"duplicate regulated tariff fingerprint: {version.fingerprint}")
        seen.add(version.fingerprint)
        versions.append(version)
    return tuple(versions)


def append_confirmed_regulated_tariff(
    options: Mapping[str, Any],
    version: ConfirmedRegulatedTariffVersion,
) -> dict[str, Any]:
    """Append one immutable confirmed regulator version without overwriting history."""
    if not isinstance(version, ConfirmedRegulatedTariffVersion):
        raise ValueError("version must be ConfirmedRegulatedTariffVersion")
    versions = list(confirmed_regulated_versions_from_options(options))
    if any(item.fingerprint == version.fingerprint for item in versions):
        return dict(options)
    versions.append(version)
    updated = dict(options)
    updated[OPTION_CONFIRMED_REGULATED_TARIFFS] = [item.as_dict() for item in versions]
    return updated


def select_confirmed_regulated_tariff_for_day(
    options: Mapping[str, Any],
    *,
    distributor: str,
    distribution_tariff: str,
    breaker_code: str,
    day: date,
) -> ConfirmedRegulatedTariffVersion:
    """Select the newest exact confirmed regulator version for one customer context."""
    if not isinstance(day, date):
        raise ValueError("day must be a date")
    matches = [
        version
        for version in confirmed_regulated_versions_from_options(options)
        if version.bundle.distributor == distributor
        and version.bundle.distribution_tariff == distribution_tariff
        and version.bundle.breaker_code == breaker_code
        and version.bundle.valid_from <= day
        and (version.bundle.valid_to is None or day <= version.bundle.valid_to)
    ]
    if not matches:
        raise LookupError(
            "no confirmed regulated tariff matches distributor/tariff/breaker/day"
        )
    newest_valid_from = max(item.bundle.valid_from for item in matches)
    newest = [item for item in matches if item.bundle.valid_from == newest_valid_from]
    if len(newest) != 1:
        raise ValueError("ambiguous confirmed regulated tariff versions for requested day")
    return newest[0]
