"""Read-only supplier tariff parser previews with exact provenance validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import re
import unicodedata

from .contracts import ElectricityContract, Supplier
from .providers.cez_tariff_parser import parse_cez_commercial_price_text
from .tariff_download import ValidatedTariffDownload
from .tariff_pdf_text import ExtractedTariffPdfText
from .tariff_sources import PRICE_SCOPE_SUPPLIER_COMMERCIAL

CONFIDENCE_EXACT = 100
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalized_product(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return " ".join(part for part in _NON_ALNUM_RE.split(ascii_text) if part)


def _decimal_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("parsed tariff price must be a finite non-negative Decimal")
    return format(value, "f")


@dataclass(frozen=True, slots=True)
class SupplierTariffParsePreview:
    """JSON-safe read-only supplier-commercial parser result."""

    supplier: str
    product_name: str
    valid_from: date
    distribution_tariff: str
    high_rate_czk_per_kwh: Decimal
    low_rate_czk_per_kwh: Decimal | None
    supplier_standing_czk_month: Decimal
    includes_vat: bool
    source_url: str
    document_sha256: str
    page_count: int
    parser_name: str
    extraction_method: str
    extraction_confidence: int
    validation_reasons: tuple[str, ...]
    price_scope: str = PRICE_SCOPE_SUPPLIER_COMMERCIAL
    parsing_performed: bool = True
    persistence_performed: bool = False
    activation_performed: bool = False

    def __post_init__(self) -> None:
        if self.supplier != Supplier.CEZ.value:
            raise ValueError("unsupported supplier parser preview")
        if not isinstance(self.product_name, str) or not self.product_name.strip():
            raise ValueError("product_name must not be empty")
        if not isinstance(self.valid_from, date):
            raise ValueError("valid_from must be a date")
        if not isinstance(self.distribution_tariff, str) or not self.distribution_tariff:
            raise ValueError("distribution_tariff must not be empty")
        _decimal_string(self.high_rate_czk_per_kwh)
        _decimal_string(self.low_rate_czk_per_kwh)
        _decimal_string(self.supplier_standing_czk_month)
        if self.includes_vat is not True:
            raise ValueError("supplier tariff preview currently requires VAT-included prices")
        if not isinstance(self.source_url, str) or not self.source_url.strip():
            raise ValueError("source_url must not be empty")
        if not isinstance(self.document_sha256, str) or not _SHA256_RE.fullmatch(
            self.document_sha256
        ):
            raise ValueError("document_sha256 must be a lowercase SHA-256 hex digest")
        if (
            isinstance(self.page_count, bool)
            or not isinstance(self.page_count, int)
            or self.page_count < 1
        ):
            raise ValueError("page_count must be a positive integer")
        if self.parser_name != "cez_commercial_v1":
            raise ValueError("unsupported parser_name")
        if self.extraction_method != "pypdf_layout":
            raise ValueError("unsupported extraction_method")
        if self.extraction_confidence != CONFIDENCE_EXACT:
            raise ValueError("parser preview must pass exact validation before exposure")
        reasons = tuple(self.validation_reasons)
        if not reasons or any(
            not isinstance(item, str) or not item.strip() for item in reasons
        ):
            raise ValueError("validation_reasons must contain non-empty strings")
        object.__setattr__(self, "validation_reasons", reasons)
        if self.price_scope != PRICE_SCOPE_SUPPLIER_COMMERCIAL:
            raise ValueError("parser preview must remain supplier-commercial")
        if self.parsing_performed is not True:
            raise ValueError("parser preview must represent a completed parse")
        if (
            self.persistence_performed is not False
            or self.activation_performed is not False
        ):
            raise ValueError("parser preview must not persist or activate a tariff")

    def as_dict(self) -> dict[str, object]:
        return {
            "supplier": self.supplier,
            "product_name": self.product_name,
            "valid_from": self.valid_from.isoformat(),
            "distribution_tariff": self.distribution_tariff,
            "high_rate_czk_per_kwh": _decimal_string(self.high_rate_czk_per_kwh),
            "low_rate_czk_per_kwh": _decimal_string(self.low_rate_czk_per_kwh),
            "supplier_standing_czk_month": _decimal_string(
                self.supplier_standing_czk_month
            ),
            "includes_vat": self.includes_vat,
            "source_url": self.source_url,
            "document_sha256": self.document_sha256,
            "page_count": self.page_count,
            "parser_name": self.parser_name,
            "extraction_method": self.extraction_method,
            "extraction_confidence": self.extraction_confidence,
            "validation_reasons": list(self.validation_reasons),
            "price_scope": self.price_scope,
            "parsing_performed": self.parsing_performed,
            "persistence_performed": self.persistence_performed,
            "activation_performed": self.activation_performed,
        }


def parse_supplier_tariff_preview(
    download: ValidatedTariffDownload,
    extracted: ExtractedTariffPdfText,
    contract: ElectricityContract,
) -> SupplierTariffParsePreview:
    """Parse and cross-check one selected supplier PDF without persistence authority."""
    if not isinstance(download, ValidatedTariffDownload):
        raise ValueError("download must be ValidatedTariffDownload")
    if not isinstance(extracted, ExtractedTariffPdfText):
        raise ValueError("extracted must be ExtractedTariffPdfText")
    if not isinstance(contract, ElectricityContract):
        raise ValueError("contract must be ElectricityContract")
    if download.parser_authorized is not True or extracted.parser_authorized is not True:
        raise ValueError("tariff parser preview requires parser-authorized inputs")
    if download.persistence_performed or download.activation_performed:
        raise ValueError("tariff parser preview cannot consume an activated download")
    if extracted.persistence_performed or extracted.activation_performed:
        raise ValueError("tariff parser preview cannot consume activated extracted text")

    candidate = download.candidate
    if candidate.price_scope != PRICE_SCOPE_SUPPLIER_COMMERCIAL:
        raise ValueError("tariff parser preview accepts only supplier-commercial candidates")
    if candidate.document.supplier != contract.supplier.value:
        raise ValueError("selected tariff supplier does not match contract")
    if download.document.supplier != candidate.document.supplier:
        raise ValueError("validated document supplier does not match selected candidate")
    if extracted.source_url != download.document.source_url:
        raise ValueError("extracted text source URL does not match validated document")
    if extracted.document_sha256 != download.document.sha256:
        raise ValueError("extracted text SHA-256 does not match validated document")

    if contract.supplier is not Supplier.CEZ:
        raise LookupError(
            f"supplier parser preview is not implemented: {contract.supplier.value}"
        )

    parsed = parse_cez_commercial_price_text(
        extracted.text,
        distribution_tariff=contract.distribution_tariff,
    )

    if _normalized_product(parsed.product_name) != _normalized_product(
        candidate.product_name
    ):
        raise ValueError("parsed ČEZ product does not match selected candidate")
    if _normalized_product(candidate.product_name) != _normalized_product(
        contract.product_name
    ):
        raise ValueError("selected ČEZ product does not match contract")
    if parsed.distribution_tariff != contract.distribution_tariff:
        raise ValueError("parsed ČEZ distribution tariff does not match contract")
    if parsed.valid_from != candidate.valid_from:
        raise ValueError("parsed ČEZ validity does not match selected candidate")

    return SupplierTariffParsePreview(
        supplier=Supplier.CEZ.value,
        product_name=parsed.product_name,
        valid_from=parsed.valid_from,
        distribution_tariff=parsed.distribution_tariff,
        high_rate_czk_per_kwh=parsed.high_rate_czk_per_kwh,
        low_rate_czk_per_kwh=parsed.low_rate_czk_per_kwh,
        supplier_standing_czk_month=parsed.supplier_standing_czk_month,
        includes_vat=parsed.includes_vat,
        source_url=download.document.source_url,
        document_sha256=download.document.sha256 or "",
        page_count=extracted.page_count,
        parser_name="cez_commercial_v1",
        extraction_method=extracted.extraction_method,
        extraction_confidence=CONFIDENCE_EXACT,
        validation_reasons=(
            "validated selected supplier-commercial PDF",
            "exact document source URL and SHA-256 match",
            "exact ČEZ product match",
            "exact distribution tariff match",
            "exact commercial-price validity match",
        ),
    )
