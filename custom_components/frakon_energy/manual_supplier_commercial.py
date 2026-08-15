"""Fail-closed manual supplier-commercial fallback for all-in tariffs.

Manual entry is deliberately limited to the three supplier-commercial values a
customer can copy from an already selected official price list: VT, NT and the
supplier standing charge.  Contract identity, official document provenance,
SHA-256, distribution tariff, breaker and regulated prices remain backend
authority and cannot be supplied through this model.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import urlparse

from .all_in_authority import AllInTariffAuthorityMethod
from .contracts import ElectricityContract, Supplier
from .pricing import FixedPriceComponent, PriceComponentKind, VariablePriceComponent
from .regulated_pricing import RegulatedTariffBundle
from .tariff_assembly import AllInTariffAssembly, assemble_all_in_tariff
from .tariff_candidate_selection import tariff_candidate_selection_fingerprint
from .tariff_download import ValidatedTariffDownload
from .tariff_provenance import MultiSourceTariffProvenance, PriceEvidence
from .tariff_sources import PRICE_SCOPE_REGULATED, PRICE_SCOPE_SUPPLIER_COMMERCIAL

_MANUAL_VALUE_FIELDS = frozenset(
    {
        "high_rate_czk_per_kwh",
        "low_rate_czk_per_kwh",
        "supplier_standing_czk_month",
    }
)
_SUPPLIER_IDENTITIES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    Supplier.CEZ.value: ("ČEZ", "ČEZ Prodej", ("cez.cz",)),
    Supplier.EON.value: ("E.ON", "E.ON Energie", ("eon.cz",)),
    Supplier.PRE.value: ("PRE", "Pražská energetika", ("pre.cz",)),
    Supplier.MND.value: ("MND", "MND", ("mnd.cz",)),
}


def _decimal(value: Any, field: str) -> Decimal:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-negative decimal string")
    try:
        parsed = Decimal(value.strip())
    except (InvalidOperation, ValueError) as err:
        raise ValueError(f"{field} must be a non-negative decimal string") from err
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{field} must be a finite non-negative decimal")
    return parsed


def _decimal_string(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("manual supplier value must be a finite non-negative Decimal")
    return format(value, "f")


def _supplier_identity(supplier: str) -> tuple[str, str, tuple[str, ...]]:
    try:
        return _SUPPLIER_IDENTITIES[supplier]
    except KeyError as err:
        raise LookupError(
            f"manual supplier-commercial fallback is not implemented for supplier: {supplier}"
        ) from err


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _validate_official_source_url(supplier: str, source_url: str) -> None:
    _display_name, _source_name, domains = _supplier_identity(supplier)
    if not isinstance(source_url, str) or not source_url.strip():
        raise ValueError("supplier source URL must not be empty")
    parsed = urlparse(source_url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("supplier source URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("supplier source URL must not contain embedded credentials")
    try:
        port = parsed.port
    except ValueError as err:
        raise ValueError("supplier source URL contains an invalid port") from err
    if port not in (None, 443):
        raise ValueError("supplier source URL must use the standard HTTPS port")
    host = parsed.hostname.lower().rstrip(".")
    if not any(_host_matches(host, domain) for domain in domains):
        raise ValueError("supplier source URL is not on the supplier's official domain")


def _supplier_document_name(download: ValidatedTariffDownload) -> str:
    path = PurePosixPath(urlparse(download.document.source_url).path)
    return path.name or "official-supplier-price-list"


def _variable_component_dict(item: VariablePriceComponent) -> dict[str, object]:
    payload = item.as_dict()
    payload["gross_vt_czk_per_kwh"] = _decimal_string(item.gross_high_rate_czk_per_kwh)
    payload["gross_nt_czk_per_kwh"] = _decimal_string(item.gross_low_rate_czk_per_kwh)
    return payload


def _fixed_component_dict(item: FixedPriceComponent) -> dict[str, object]:
    payload = item.as_dict()
    payload["gross_monthly_czk"] = _decimal_string(item.gross_monthly_czk)
    return payload


@dataclass(frozen=True, slots=True)
class ManualSupplierCommercialValues:
    """The only price fields the manual fallback accepts from a customer."""

    high_rate_czk_per_kwh: Decimal
    low_rate_czk_per_kwh: Decimal
    supplier_standing_czk_month: Decimal

    def __post_init__(self) -> None:
        for field_name in _MANUAL_VALUE_FIELDS:
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{field_name} must be a finite non-negative Decimal")

    def as_dict(self) -> dict[str, str]:
        return {
            "high_rate_czk_per_kwh": _decimal_string(self.high_rate_czk_per_kwh),
            "low_rate_czk_per_kwh": _decimal_string(self.low_rate_czk_per_kwh),
            "supplier_standing_czk_month": _decimal_string(
                self.supplier_standing_czk_month
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ManualSupplierCommercialValues":
        if not isinstance(value, Mapping):
            raise ValueError("manual supplier-commercial values must be an object")
        unexpected = set(value) - _MANUAL_VALUE_FIELDS
        missing = _MANUAL_VALUE_FIELDS - set(value)
        if unexpected:
            raise ValueError(
                "manual supplier-commercial values contain unsupported fields: "
                + ", ".join(sorted(str(item) for item in unexpected))
            )
        if missing:
            raise ValueError(
                "manual supplier-commercial values are missing fields: "
                + ", ".join(sorted(missing))
            )
        return cls(
            high_rate_czk_per_kwh=_decimal(
                value.get("high_rate_czk_per_kwh"), "high_rate_czk_per_kwh"
            ),
            low_rate_czk_per_kwh=_decimal(
                value.get("low_rate_czk_per_kwh"), "low_rate_czk_per_kwh"
            ),
            supplier_standing_czk_month=_decimal(
                value.get("supplier_standing_czk_month"),
                "supplier_standing_czk_month",
            ),
        )


@dataclass(frozen=True, slots=True)
class ManualSupplierCommercialPreview:
    """Complete read-only all-in preview with explicit manual authority."""

    assembly: AllInTariffAssembly
    supplier_document_sha256: str
    supplier_source_url: str
    regulated_source_url: str
    regulated_checksum: str | None
    validation_reasons: tuple[str, ...]
    authority_method: AllInTariffAuthorityMethod = (
        AllInTariffAuthorityMethod.MANUAL_USER_ENTRY
    )
    parsing_performed: bool = False
    persistence_performed: bool = False
    activation_performed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.assembly, AllInTariffAssembly):
            raise ValueError("assembly must be AllInTariffAssembly")
        if (
            not isinstance(self.supplier_document_sha256, str)
            or len(self.supplier_document_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.supplier_document_sha256)
        ):
            raise ValueError("supplier_document_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.supplier_source_url, str) or not self.supplier_source_url.strip():
            raise ValueError("supplier_source_url must not be empty")
        if not isinstance(self.regulated_source_url, str) or not self.regulated_source_url.strip():
            raise ValueError("regulated_source_url must not be empty")
        reasons = tuple(self.validation_reasons)
        if not reasons or any(not isinstance(item, str) or not item.strip() for item in reasons):
            raise ValueError("validation_reasons must contain non-empty strings")
        object.__setattr__(self, "validation_reasons", reasons)
        if self.authority_method is not AllInTariffAuthorityMethod.MANUAL_USER_ENTRY:
            raise ValueError("manual preview must use manual_user_entry authority")
        if self.parsing_performed or self.persistence_performed or self.activation_performed:
            raise ValueError("manual preview must not parse, persist or activate a tariff")

    def as_dict(self) -> dict[str, object]:
        return {
            "supplier": self.assembly.supplier,
            "product_name": self.assembly.product_name,
            "distribution_tariff": self.assembly.distribution_tariff,
            "breaker_code": self.assembly.breaker_code,
            "valid_from": self.assembly.valid_from.isoformat(),
            "valid_to": (
                self.assembly.valid_to.isoformat()
                if self.assembly.valid_to is not None
                else None
            ),
            "all_in_vt_czk_kwh": _decimal_string(self.assembly.all_in_vt_czk_kwh),
            "all_in_nt_czk_kwh": _decimal_string(self.assembly.all_in_nt_czk_kwh),
            "fixed_monthly_total_czk": _decimal_string(
                self.assembly.fixed_monthly_total_czk
            ),
            "variable_components": [
                _variable_component_dict(item) for item in self.assembly.variable_components
            ],
            "fixed_components": [
                _fixed_component_dict(item) for item in self.assembly.fixed_components
            ],
            "supplier_source_url": self.supplier_source_url,
            "supplier_document_sha256": self.supplier_document_sha256,
            "regulated_source_url": self.regulated_source_url,
            "regulated_checksum": self.regulated_checksum,
            "provenance": self.assembly.provenance.as_dict(),
            "validation_reasons": list(self.validation_reasons),
            "authority_method": self.authority_method.value,
            "manual_entry_performed": True,
            "parsing_performed": self.parsing_performed,
            "all_in_ready": self.assembly.all_in_ready,
            "persistence_performed": self.persistence_performed,
            "activation_performed": self.activation_performed,
        }


def build_manual_supplier_commercial_preview(
    *,
    download: ValidatedTariffDownload,
    contract: ElectricityContract,
    regulated: RegulatedTariffBundle,
    regulated_evidence: tuple[PriceEvidence, ...],
    values: ManualSupplierCommercialValues,
) -> ManualSupplierCommercialPreview:
    """Build all-in pricing from three manual commercial values and backend authority."""

    if not isinstance(download, ValidatedTariffDownload):
        raise ValueError("download must be ValidatedTariffDownload")
    if not isinstance(contract, ElectricityContract):
        raise ValueError("contract must be ElectricityContract")
    if not isinstance(regulated, RegulatedTariffBundle):
        raise ValueError("regulated must be RegulatedTariffBundle")
    if not isinstance(values, ManualSupplierCommercialValues):
        raise ValueError("values must be ManualSupplierCommercialValues")
    if download.persistence_performed or download.activation_performed:
        raise ValueError("manual preview cannot consume an activated download")

    candidate = download.candidate
    expected_fingerprint = tariff_candidate_selection_fingerprint(candidate)
    if download.selected_fingerprint != expected_fingerprint:
        raise ValueError("selected fingerprint does not match tariff candidate")
    if candidate.match_score != 100:
        raise ValueError("manual fallback requires an exact 100-score supplier candidate")
    if candidate.price_scope != PRICE_SCOPE_SUPPLIER_COMMERCIAL:
        raise ValueError("manual fallback requires a supplier-commercial candidate")
    if candidate.document.supplier != contract.supplier.value:
        raise ValueError("selected supplier document does not match contract supplier")
    if candidate.product_name.strip() != contract.product_name.strip():
        raise ValueError("selected supplier product does not match contract product")
    if download.document.supplier != candidate.document.supplier:
        raise ValueError("validated supplier document identity drifted from candidate")
    if download.document.source_url != candidate.document.source_url:
        raise ValueError("validated supplier source URL drifted from candidate")
    if candidate.document.sha256 is not None and (
        candidate.document.sha256 != download.document.sha256
    ):
        raise ValueError("validated supplier SHA-256 drifted from selected candidate")
    if download.document.sha256 is None:
        raise ValueError("manual fallback requires a SHA-256 pinned supplier PDF")
    _validate_official_source_url(contract.supplier.value, download.document.source_url)

    if not regulated.confirmed:
        raise ValueError("regulated tariff bundle must be confirmed for manual fallback")
    if regulated.distributor != contract.distributor.value:
        raise ValueError("regulated distributor does not match customer contract")
    if regulated.distribution_tariff != contract.distribution_tariff:
        raise ValueError("regulated distribution tariff does not match customer contract")
    if regulated.breaker_code != contract.breaker.code:
        raise ValueError("regulated breaker does not match customer contract")

    evidence = tuple(regulated_evidence)
    if not evidence or any(not isinstance(item, PriceEvidence) for item in evidence):
        raise ValueError("regulated_evidence must contain PriceEvidence records")
    if any(item.scope != PRICE_SCOPE_REGULATED for item in evidence):
        raise ValueError("regulated_evidence must contain only regulated evidence")
    if not any(item.source_url == regulated.source_url for item in evidence):
        raise ValueError("regulated evidence does not contain the regulated bundle source")
    if regulated.checksum is not None and not any(
        item.source_url == regulated.source_url and item.checksum == regulated.checksum
        for item in evidence
    ):
        raise ValueError("regulated evidence checksum does not match regulated bundle")

    display_name, supplier_source_name, _domains = _supplier_identity(
        contract.supplier.value
    )
    supplier_evidence = PriceEvidence(
        scope=PRICE_SCOPE_SUPPLIER_COMMERCIAL,
        source_name=supplier_source_name,
        document_name=_supplier_document_name(download),
        source_url=download.document.source_url,
        valid_from=candidate.valid_from,
        valid_to=candidate.valid_to,
        document_date=download.document.document_date,
        checksum=download.document.sha256,
    )
    provenance = MultiSourceTariffProvenance((supplier_evidence, *evidence))
    commodity = VariablePriceComponent(
        kind=PriceComponentKind.COMMODITY,
        name=f"{display_name} – ručně potvrzená obchodní cena elektřiny",
        high_rate_czk_per_kwh=values.high_rate_czk_per_kwh,
        low_rate_czk_per_kwh=values.low_rate_czk_per_kwh,
        includes_vat=True,
    )
    supplier_fixed = FixedPriceComponent(
        kind=PriceComponentKind.SUPPLIER_FIXED,
        name=f"{display_name} – ručně potvrzená stálá platba dodavatele",
        monthly_czk=values.supplier_standing_czk_month,
        includes_vat=True,
    )
    assembly = assemble_all_in_tariff(
        supplier=contract.supplier.value,
        product_name=candidate.product_name,
        distribution_tariff=contract.distribution_tariff,
        breaker_code=contract.breaker.code,
        commercial_valid_from=candidate.valid_from,
        commercial_valid_to=candidate.valid_to,
        commodity=commodity,
        supplier_fixed=supplier_fixed,
        regulated=regulated,
        provenance=provenance,
    )
    return ManualSupplierCommercialPreview(
        assembly=assembly,
        supplier_document_sha256=download.document.sha256,
        supplier_source_url=download.document.source_url,
        regulated_source_url=regulated.source_url,
        regulated_checksum=regulated.checksum,
        validation_reasons=(
            "manual input limited to VAT-included supplier VT, NT and standing charge",
            "exact 100-score supplier candidate selected by immutable fingerprint",
            "validated official supplier PDF pinned by SHA-256",
            "confirmed regulated tariff bundle supplied independently by backend",
            "exact distributor, distribution tariff and breaker match",
            "supplier and regulated validity periods overlap",
            "manual values are explicitly marked manual_user_entry and were not parsed",
        ),
    )
