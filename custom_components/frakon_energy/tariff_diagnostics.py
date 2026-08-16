"""Read-only diagnostics for the exact confirmed customer electricity tariff."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Mapping

from .all_in_authority import (
    AllInTariffAuthorityMethod,
    all_in_tariff_authority_from_options,
)
from .all_in_catalog import (
    all_in_tariff_fingerprint,
    confirmed_all_in_tariff_for_context_from_options,
)
from .contracts import confirmed_contract_from_options, contract_fingerprint
from .tariff_parser_preview import supplier_parser_supported
from .tariff_provenance import PriceEvidence
from .tariff_source_watch import (
    TariffSourceCheckResult,
    source_watch_from_confirmed_all_in,
    tariff_source_watch_fingerprint,
)
from .tariff_source_watch_store import (
    TariffSourceWatchRecord,
    tariff_source_watch_records_from_options,
)
from .tariff_sources import PRICE_SCOPE_REGULATED, PRICE_SCOPE_SUPPLIER_COMMERCIAL

WATCH_BINDING_CURRENT = "current"
WATCH_BINDING_MISSING = "missing"
WATCH_BINDING_STALE_CHECKSUM = "stale_checksum"
PARSER_STATUS_VERIFIED = "verified_parser_active"
PARSER_STATUS_MANUAL = "manual_user_entry"


def _decimal_string(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("diagnostic price must be a finite non-negative Decimal")
    return format(value, "f")


def _evidence_dict(evidence: PriceEvidence) -> dict[str, Any]:
    return evidence.as_dict()


def _last_check_dict(value: TariffSourceCheckResult | None) -> dict[str, Any] | None:
    return None if value is None else value.as_dict()


@dataclass(frozen=True, slots=True)
class TariffDiagnosticsSnapshot:
    """One exact, read-only view of customer tariff authority and source health."""

    day: date
    contract_fingerprint: str
    all_in_fingerprint: str
    authority_method: AllInTariffAuthorityMethod
    supplier: str
    product_name: str
    distributor: str
    distribution_tariff: str
    breaker_code: str
    all_in_vt_czk_kwh: Decimal
    all_in_nt_czk_kwh: Decimal
    fixed_monthly_total_czk: Decimal
    supplier_evidence: PriceEvidence
    regulated_evidence: tuple[PriceEvidence, ...]
    parser_supported: bool
    parser_status: str
    watch_fingerprint: str
    watch_binding: str
    watch_record: TariffSourceWatchRecord | None

    def __post_init__(self) -> None:
        if not isinstance(self.day, date):
            raise ValueError("day must be a date")
        for field_name in ("contract_fingerprint", "all_in_fingerprint", "watch_fingerprint"):
            value = getattr(self, field_name)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
            ):
                raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
        if not isinstance(self.authority_method, AllInTariffAuthorityMethod):
            raise ValueError("authority_method must be AllInTariffAuthorityMethod")
        for field_name in (
            "supplier",
            "product_name",
            "distributor",
            "distribution_tariff",
            "breaker_code",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")
        _decimal_string(self.all_in_vt_czk_kwh)
        _decimal_string(self.all_in_nt_czk_kwh)
        _decimal_string(self.fixed_monthly_total_czk)
        if not isinstance(self.supplier_evidence, PriceEvidence):
            raise ValueError("supplier_evidence must be PriceEvidence")
        regulated = tuple(self.regulated_evidence)
        if not regulated or any(not isinstance(item, PriceEvidence) for item in regulated):
            raise ValueError("regulated_evidence must contain PriceEvidence")
        if any(item.scope != PRICE_SCOPE_REGULATED for item in regulated):
            raise ValueError("regulated_evidence must contain only regulated evidence")
        object.__setattr__(self, "regulated_evidence", regulated)
        if not isinstance(self.parser_supported, bool):
            raise ValueError("parser_supported must be boolean")
        if self.parser_status not in {PARSER_STATUS_VERIFIED, PARSER_STATUS_MANUAL}:
            raise ValueError("unsupported parser_status")
        if self.watch_binding not in {
            WATCH_BINDING_CURRENT,
            WATCH_BINDING_MISSING,
            WATCH_BINDING_STALE_CHECKSUM,
        }:
            raise ValueError("unsupported watch_binding")
        if self.watch_record is not None and not isinstance(
            self.watch_record, TariffSourceWatchRecord
        ):
            raise ValueError("watch_record must be TariffSourceWatchRecord")

    def as_dict(self) -> dict[str, Any]:
        record = self.watch_record
        watch = record.watch if record is not None else None
        last_check = record.last_check if record is not None else None
        return {
            "day": self.day.isoformat(),
            "contract_fingerprint": self.contract_fingerprint,
            "all_in_tariff_fingerprint": self.all_in_fingerprint,
            "authority_method": self.authority_method.value,
            "supplier": self.supplier,
            "product_name": self.product_name,
            "distributor": self.distributor,
            "distribution_tariff": self.distribution_tariff,
            "breaker_code": self.breaker_code,
            "all_in_vt_czk_kwh": _decimal_string(self.all_in_vt_czk_kwh),
            "all_in_nt_czk_kwh": _decimal_string(self.all_in_nt_czk_kwh),
            "fixed_monthly_total_czk": _decimal_string(self.fixed_monthly_total_czk),
            "supplier_source": _evidence_dict(self.supplier_evidence),
            "regulated_sources": [
                _evidence_dict(item) for item in self.regulated_evidence
            ],
            "parser": {
                "supported": self.parser_supported,
                "status": self.parser_status,
            },
            "source_watch": {
                "fingerprint": self.watch_fingerprint,
                "binding": self.watch_binding,
                "registered": record is not None,
                "etag": watch.etag if watch is not None else None,
                "last_modified": watch.last_modified if watch is not None else None,
                "last_check": _last_check_dict(last_check),
                "pending_sha256": record.pending_sha256 if record is not None else None,
                "pending_detected_at": (
                    record.pending_detected_at.isoformat()
                    if record is not None and record.pending_detected_at is not None
                    else None
                ),
            },
            "read_only": True,
            "persistence_performed": False,
            "activation_performed": False,
        }


def build_tariff_diagnostics(
    options: Mapping[str, Any],
    *,
    day: date,
) -> TariffDiagnosticsSnapshot:
    """Resolve exact confirmed tariff diagnostics without reconciling stored state."""

    if not isinstance(options, Mapping):
        raise ValueError("options must be a mapping")
    if not isinstance(day, date):
        raise ValueError("day must be a date")

    contract = confirmed_contract_from_options(options, day)
    all_in = confirmed_all_in_tariff_for_context_from_options(
        options,
        supplier=contract.supplier.value,
        product_name=contract.product_name,
        distribution_tariff=contract.distribution_tariff,
        breaker_code=contract.breaker.code,
        day=day,
    )
    all_in_fp = all_in_tariff_fingerprint(all_in)
    authority = all_in_tariff_authority_from_options(options, all_in_fp)
    assembly = all_in.assembly

    supplier_sources = assembly.provenance.evidence_for_scope(
        PRICE_SCOPE_SUPPLIER_COMMERCIAL
    )
    if len(supplier_sources) != 1:
        raise ValueError("confirmed all-in tariff must have exactly one supplier source")
    supplier_source = supplier_sources[0]
    if supplier_source.checksum is None:
        raise ValueError("confirmed supplier source is missing checksum")
    regulated_sources = assembly.provenance.evidence_for_scope(PRICE_SCOPE_REGULATED)
    if not regulated_sources:
        raise ValueError("confirmed all-in tariff is missing regulated source evidence")

    expected_watch = source_watch_from_confirmed_all_in(
        all_in,
        supplier=contract.supplier.value,
    )
    watch_fp = tariff_source_watch_fingerprint(expected_watch)
    record = next(
        (
            item
            for item in tariff_source_watch_records_from_options(options)
            if tariff_source_watch_fingerprint(item.watch) == watch_fp
        ),
        None,
    )
    if record is None:
        watch_binding = WATCH_BINDING_MISSING
    elif record.watch.active_sha256 == expected_watch.active_sha256:
        watch_binding = WATCH_BINDING_CURRENT
    else:
        watch_binding = WATCH_BINDING_STALE_CHECKSUM

    parser_supported = supplier_parser_supported(contract.supplier)
    if authority.method is AllInTariffAuthorityMethod.VERIFIED_PARSER:
        if not parser_supported:
            raise ValueError(
                "verified parser authority references a supplier without parser support"
            )
        parser_status = PARSER_STATUS_VERIFIED
    elif authority.method is AllInTariffAuthorityMethod.MANUAL_USER_ENTRY:
        parser_status = PARSER_STATUS_MANUAL
    else:
        raise ValueError("unsupported all-in tariff authority method")

    return TariffDiagnosticsSnapshot(
        day=day,
        contract_fingerprint=contract_fingerprint(contract),
        all_in_fingerprint=all_in_fp,
        authority_method=authority.method,
        supplier=assembly.supplier,
        product_name=assembly.product_name,
        distributor=contract.distributor.value,
        distribution_tariff=assembly.distribution_tariff,
        breaker_code=assembly.breaker_code,
        all_in_vt_czk_kwh=assembly.all_in_vt_czk_kwh,
        all_in_nt_czk_kwh=assembly.all_in_nt_czk_kwh,
        fixed_monthly_total_czk=assembly.fixed_monthly_total_czk,
        supplier_evidence=supplier_source,
        regulated_evidence=regulated_sources,
        parser_supported=parser_supported,
        parser_status=parser_status,
        watch_fingerprint=watch_fp,
        watch_binding=watch_binding,
        watch_record=record,
    )
