"""Read-only all-in tariff preview from independently verified price sources."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import PurePosixPath
from urllib.parse import urlparse

from .contracts import ElectricityContract, Supplier
from .pricing import FixedPriceComponent, PriceComponentKind, VariablePriceComponent
from .regulated_pricing import RegulatedTariffBundle
from .tariff_assembly import AllInTariffAssembly, assemble_all_in_tariff
from .tariff_download import ValidatedTariffDownload
from .tariff_parser_preview import SupplierTariffParsePreview
from .tariff_provenance import MultiSourceTariffProvenance, PriceEvidence
from .tariff_sources import PRICE_SCOPE_REGULATED, PRICE_SCOPE_SUPPLIER_COMMERCIAL


@dataclass(frozen=True, slots=True)
class AllInTariffPreview:
    """JSON-safe complete price preview with no persistence or activation authority."""

    assembly: AllInTariffAssembly
    supplier_document_sha256: str
    supplier_source_url: str
    regulated_source_url: str
    regulated_checksum: str | None
    validation_reasons: tuple[str, ...]
    persistence_performed: bool = False
    activation_performed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.assembly, AllInTariffAssembly):
            raise ValueError("assembly must be AllInTariffAssembly")
        if not isinstance(self.supplier_document_sha256, str) or len(self.supplier_document_sha256) != 64:
            raise ValueError("supplier_document_sha256 must be a SHA-256 digest")
        if not isinstance(self.supplier_source_url, str) or not self.supplier_source_url.strip():
            raise ValueError("supplier_source_url must not be empty")
        if not isinstance(self.regulated_source_url, str) or not self.regulated_source_url.strip():
            raise ValueError("regulated_source_url must not be empty")
        reasons = tuple(self.validation_reasons)
        if not reasons or any(not isinstance(item, str) or not item.strip() for item in reasons):
            raise ValueError("validation_reasons must contain non-empty strings")
        object.__setattr__(self, "validation_reasons", reasons)
        if self.persistence_performed is not False or self.activation_performed is not False:
            raise ValueError("all-in preview must not persist or activate a tariff")

    def as_dict(self) -> dict[str, object]:
        return {
            "supplier": self.assembly.supplier,
            "product_name": self.assembly.product_name,
            "distribution_tariff": self.assembly.distribution_tariff,
            "breaker_code": self.assembly.breaker_code,
            "valid_from": self.assembly.valid_from.isoformat(),
            "valid_to": self.assembly.valid_to.isoformat() if self.assembly.valid_to else None,
            "all_in_vt_czk_kwh": _decimal_string(self.assembly.all_in_vt_czk_kwh),
            "all_in_nt_czk_kwh": _decimal_string(self.assembly.all_in_nt_czk_kwh),
            "fixed_monthly_total_czk": _decimal_string(self.assembly.fixed_monthly_total_czk),
            "variable_components": [_variable_component_dict(item) for item in self.assembly.variable_components],
            "fixed_components": [_fixed_component_dict(item) for item in self.assembly.fixed_components],
            "supplier_source_url": self.supplier_source_url,
            "supplier_document_sha256": self.supplier_document_sha256,
            "regulated_source_url": self.regulated_source_url,
            "regulated_checksum": self.regulated_checksum,
            "provenance": self.assembly.provenance.as_dict(),
            "validation_reasons": list(self.validation_reasons),
            "all_in_ready": self.assembly.all_in_ready,
            "persistence_performed": self.persistence_performed,
            "activation_performed": self.activation_performed,
        }


def _decimal_string(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("all-in preview values must be finite non-negative Decimals")
    return format(value, "f")


def _variable_component_dict(item: VariablePriceComponent) -> dict[str, object]:
    payload = item.as_dict()
    payload["gross_vt_czk_per_kwh"] = _decimal_string(item.gross_high_rate_czk_per_kwh)
    payload["gross_nt_czk_per_kwh"] = _decimal_string(item.gross_low_rate_czk_per_kwh)
    return payload


def _fixed_component_dict(item: FixedPriceComponent) -> dict[str, object]:
    payload = item.as_dict()
    payload["gross_monthly_czk"] = _decimal_string(item.gross_monthly_czk)
    return payload


def _supplier_document_name(download: ValidatedTariffDownload) -> str:
    path = PurePosixPath(urlparse(download.document.source_url).path)
    return path.name or "official-supplier-price-list"


def _commercial_components(parsed: SupplierTariffParsePreview) -> tuple[VariablePriceComponent, FixedPriceComponent]:
    if parsed.supplier != Supplier.CEZ.value:
        raise LookupError(f"all-in preview is not implemented for supplier: {parsed.supplier}")
    if parsed.low_rate_czk_per_kwh is None:
        raise ValueError("all-in preview requires an explicit low-rate supplier price")
    if parsed.includes_vat is not True:
        raise ValueError("all-in preview requires VAT-included supplier prices")
    return (
        VariablePriceComponent(
            kind=PriceComponentKind.COMMODITY,
            name="ČEZ – obchodní cena elektřiny",
            high_rate_czk_per_kwh=parsed.high_rate_czk_per_kwh,
            low_rate_czk_per_kwh=parsed.low_rate_czk_per_kwh,
            includes_vat=True,
        ),
        FixedPriceComponent(
            kind=PriceComponentKind.SUPPLIER_FIXED,
            name="ČEZ – stálá platba dodavatele",
            monthly_czk=parsed.supplier_standing_czk_month,
            includes_vat=True,
        ),
    )


def build_all_in_tariff_preview(
    *,
    download: ValidatedTariffDownload,
    parsed: SupplierTariffParsePreview,
    contract: ElectricityContract,
    regulated: RegulatedTariffBundle,
    regulated_evidence: tuple[PriceEvidence, ...],
) -> AllInTariffPreview:
    """Assemble a complete preview without granting persistence/activation authority."""
    if not isinstance(download, ValidatedTariffDownload):
        raise ValueError("download must be ValidatedTariffDownload")
    if not isinstance(parsed, SupplierTariffParsePreview):
        raise ValueError("parsed must be SupplierTariffParsePreview")
    if not isinstance(contract, ElectricityContract):
        raise ValueError("contract must be ElectricityContract")
    if not isinstance(regulated, RegulatedTariffBundle):
        raise ValueError("regulated must be RegulatedTariffBundle")
    if download.persistence_performed or download.activation_performed:
        raise ValueError("all-in preview cannot consume an activated download")
    if parsed.persistence_performed or parsed.activation_performed:
        raise ValueError("all-in preview cannot consume an activated parser result")
    if not regulated.confirmed:
        raise ValueError("regulated tariff bundle must be confirmed for all-in preview")

    candidate = download.candidate
    if candidate.price_scope != PRICE_SCOPE_SUPPLIER_COMMERCIAL:
        raise ValueError("all-in preview requires a supplier-commercial candidate")
    if candidate.document.supplier != contract.supplier.value:
        raise ValueError("selected supplier document does not match contract supplier")
    if parsed.supplier != contract.supplier.value:
        raise ValueError("parsed supplier price does not match contract supplier")
    if parsed.product_name != candidate.product_name:
        raise ValueError("parsed supplier product does not match selected candidate")
    if parsed.distribution_tariff != contract.distribution_tariff:
        raise ValueError("parsed distribution tariff does not match contract")
    if parsed.valid_from != candidate.valid_from:
        raise ValueError("parsed supplier validity does not match selected candidate")
    if parsed.source_url != download.document.source_url:
        raise ValueError("parsed supplier source URL does not match validated document")
    if parsed.document_sha256 != download.document.sha256:
        raise ValueError("parsed supplier SHA-256 does not match validated document")

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

    supplier_evidence = PriceEvidence(
        scope=PRICE_SCOPE_SUPPLIER_COMMERCIAL,
        source_name="ČEZ Prodej",
        document_name=_supplier_document_name(download),
        source_url=download.document.source_url,
        valid_from=candidate.valid_from,
        valid_to=candidate.valid_to,
        document_date=download.document.document_date,
        checksum=download.document.sha256,
    )
    provenance = MultiSourceTariffProvenance((supplier_evidence, *evidence))
    commodity, supplier_fixed = _commercial_components(parsed)
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
    return AllInTariffPreview(
        assembly=assembly,
        supplier_document_sha256=download.document.sha256 or "",
        supplier_source_url=download.document.source_url,
        regulated_source_url=regulated.source_url,
        regulated_checksum=regulated.checksum,
        validation_reasons=(
            "validated supplier-commercial PDF and SHA-256",
            "exact parser preview matches selected supplier candidate",
            "confirmed regulated tariff bundle",
            "exact distribution tariff and breaker match",
            "supplier and regulated provenance linked to official sources",
            "commercial and regulated validity periods overlap",
        ),
    )
