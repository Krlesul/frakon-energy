"""Read-only ČEZ commercial tariff parse preview.

The preview is deliberately downstream of the selected-download and bounded PDF
text boundaries. It may expose extracted supplier-commercial values to the UI,
but it never persists, confirms or activates a customer tariff.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from .contracts import ElectricityContract, Supplier, contract_fingerprint
from .providers.cez_tariff_parser import (
    ParsedCezCommercialPrice,
    parse_cez_commercial_price_text,
)
from .tariff_candidate_selection import tariff_candidate_selection_fingerprint
from .tariff_download import ValidatedTariffDownload
from .tariff_pdf_text import (
    ExtractedTariffPdfText,
    extract_validated_tariff_pdf_text,
)
from .tariff_sources import PRICE_SCOPE_SUPPLIER_COMMERCIAL


def _same_product(left: str, right: str) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return left.strip().casefold() == right.strip().casefold()


def _decimal_string(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


@dataclass(frozen=True, slots=True)
class CezCommercialTariffPreview:
    """UI-safe supplier-commercial values parsed from one selected ČEZ PDF."""

    contract_fingerprint: str
    candidate_fingerprint: str
    source_url: str
    document_sha256: str
    page_count: int
    product_name: str
    valid_from: date
    distribution_tariff: str
    high_rate_czk_per_kwh: Decimal
    low_rate_czk_per_kwh: Decimal | None
    supplier_standing_czk_month: Decimal
    includes_vat: bool
    price_scope: str = PRICE_SCOPE_SUPPLIER_COMMERCIAL
    all_in_ready: bool = False
    download_performed: bool = True
    parsing_performed: bool = True
    persistence_performed: bool = False
    activation_performed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.contract_fingerprint, str) or len(self.contract_fingerprint) != 64:
            raise ValueError("contract_fingerprint must be a SHA-256 digest")
        if not isinstance(self.candidate_fingerprint, str) or len(self.candidate_fingerprint) != 64:
            raise ValueError("candidate_fingerprint must be a SHA-256 digest")
        if not isinstance(self.source_url, str) or not self.source_url.strip():
            raise ValueError("source_url must not be empty")
        if not isinstance(self.document_sha256, str) or len(self.document_sha256) != 64:
            raise ValueError("document_sha256 must be a SHA-256 digest")
        if isinstance(self.page_count, bool) or not isinstance(self.page_count, int) or self.page_count <= 0:
            raise ValueError("page_count must be a positive integer")
        if not isinstance(self.product_name, str) or not self.product_name.strip():
            raise ValueError("product_name must not be empty")
        if not isinstance(self.valid_from, date):
            raise ValueError("valid_from must be a date")
        if not isinstance(self.distribution_tariff, str) or not self.distribution_tariff.strip():
            raise ValueError("distribution_tariff must not be empty")
        for field_name in ("high_rate_czk_per_kwh", "supplier_standing_czk_month"):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{field_name} must be a finite non-negative Decimal")
        if self.low_rate_czk_per_kwh is not None:
            value = self.low_rate_czk_per_kwh
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError("low_rate_czk_per_kwh must be a finite non-negative Decimal")
        if self.includes_vat is not True:
            raise ValueError("ČEZ commercial preview requires VAT-included source values")
        if self.price_scope != PRICE_SCOPE_SUPPLIER_COMMERCIAL:
            raise ValueError("ČEZ parse preview must remain supplier-commercial")
        if self.all_in_ready is not False:
            raise ValueError("supplier-commercial preview must not be marked all-in ready")
        if self.download_performed is not True or self.parsing_performed is not True:
            raise ValueError("parse preview must represent a completed validated download and parse")
        if self.persistence_performed is not False or self.activation_performed is not False:
            raise ValueError("parse preview has no persistence or activation authority")

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_fingerprint": self.contract_fingerprint,
            "candidate_fingerprint": self.candidate_fingerprint,
            "source_url": self.source_url,
            "document_sha256": self.document_sha256,
            "page_count": self.page_count,
            "product_name": self.product_name,
            "valid_from": self.valid_from.isoformat(),
            "distribution_tariff": self.distribution_tariff,
            "high_rate_czk_per_kwh": _decimal_string(self.high_rate_czk_per_kwh),
            "low_rate_czk_per_kwh": _decimal_string(self.low_rate_czk_per_kwh),
            "supplier_standing_czk_month": _decimal_string(self.supplier_standing_czk_month),
            "includes_vat": self.includes_vat,
            "price_scope": self.price_scope,
            "all_in_ready": self.all_in_ready,
            "download_performed": self.download_performed,
            "parsing_performed": self.parsing_performed,
            "persistence_performed": self.persistence_performed,
            "activation_performed": self.activation_performed,
        }


def preview_cez_commercial_tariff_text(
    extracted: ExtractedTariffPdfText,
    *,
    download: ValidatedTariffDownload,
    contract: ElectricityContract,
    day: date,
) -> CezCommercialTariffPreview:
    """Parse and bind extracted ČEZ text to the exact contract/candidate version."""
    if not isinstance(extracted, ExtractedTariffPdfText):
        raise ValueError("extracted must be ExtractedTariffPdfText")
    if not isinstance(download, ValidatedTariffDownload):
        raise ValueError("download must be ValidatedTariffDownload")
    if not isinstance(contract, ElectricityContract):
        raise ValueError("contract must be ElectricityContract")
    if not isinstance(day, date):
        raise ValueError("day must be a date")
    if contract.supplier != Supplier.CEZ:
        raise ValueError("ČEZ parse preview accepts only ČEZ contracts")
    if not contract.applies_on(day):
        raise ValueError("contract does not apply on parse-preview day")
    if download.parser_authorized is not True:
        raise ValueError("selected tariff download is not parser-authorized")
    if download.candidate.document.supplier != Supplier.CEZ.value:
        raise ValueError("selected tariff candidate is not a ČEZ document")
    if download.candidate.price_scope != PRICE_SCOPE_SUPPLIER_COMMERCIAL:
        raise ValueError("selected ČEZ candidate is not supplier-commercial")
    if day < download.candidate.valid_from or (
        download.candidate.valid_to is not None and day > download.candidate.valid_to
    ):
        raise ValueError("selected ČEZ candidate does not apply on parse-preview day")
    if extracted.parser_authorized is not True:
        raise ValueError("extracted tariff text is not parser-authorized")
    if extracted.source_url != download.document.source_url:
        raise ValueError("extracted tariff source does not match validated download")
    if extracted.document_sha256 != download.document.sha256:
        raise ValueError("extracted tariff checksum does not match validated download")

    parsed: ParsedCezCommercialPrice = parse_cez_commercial_price_text(
        extracted.text,
        distribution_tariff=contract.distribution_tariff,
    )
    if not _same_product(parsed.product_name, download.candidate.product_name):
        raise ValueError("parsed ČEZ product does not match selected tariff candidate")
    if parsed.valid_from != download.candidate.valid_from:
        raise ValueError("parsed ČEZ validity does not match selected tariff candidate")
    if parsed.distribution_tariff != contract.distribution_tariff:
        raise ValueError("parsed ČEZ distribution tariff does not match contract")
    if parsed.valid_from > day:
        raise ValueError("parsed ČEZ price list is not yet valid on parse-preview day")

    return CezCommercialTariffPreview(
        contract_fingerprint=contract_fingerprint(contract),
        candidate_fingerprint=tariff_candidate_selection_fingerprint(download.candidate),
        source_url=download.document.source_url,
        document_sha256=download.document.sha256 or "",
        page_count=extracted.page_count,
        product_name=parsed.product_name,
        valid_from=parsed.valid_from,
        distribution_tariff=parsed.distribution_tariff,
        high_rate_czk_per_kwh=parsed.high_rate_czk_per_kwh,
        low_rate_czk_per_kwh=parsed.low_rate_czk_per_kwh,
        supplier_standing_czk_month=parsed.supplier_standing_czk_month,
        includes_vat=parsed.includes_vat,
    )


def preview_validated_cez_commercial_tariff(
    download: ValidatedTariffDownload,
    *,
    contract: ElectricityContract,
    day: date,
) -> CezCommercialTariffPreview:
    """Run bounded PDF text extraction and return a read-only ČEZ price preview."""
    extracted = extract_validated_tariff_pdf_text(download)
    return preview_cez_commercial_tariff_text(
        extracted,
        download=download,
        contract=contract,
        day=day,
    )
