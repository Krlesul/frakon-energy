"""Manual supplier-commercial fallback with verified document provenance.

Manual mode is deliberately narrower than the automatic parser path. The user
may supply only the supplier-commercial VT, NT and monthly standing charge. The
selected supplier document, product identity, validity, distribution tariff,
breaker and all regulated components remain server-authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import PurePosixPath
import re
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

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SUPPLIER_IDENTITIES = {
    Supplier.CEZ.value: ("ČEZ", "ČEZ Prodej", ("cez.cz",)),
    Supplier.EON.value: ("E.ON", "E.ON Energie", ("eon.cz",)),
    Supplier.PRE.value: ("PRE", "Pražská energetika", ("pre.cz",)),
    Supplier.MND.value: ("MND", "MND Energie", ("mnd.cz",)),
}


def _manual_decimal(value: Decimal, field_name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise ValueError(f"{field_name} must be Decimal")
    if not value.is_finite() or value < 0:
        raise ValueError(f"{field_name} must be finite and non-negative")
    return value


def _decimal_string(value: Decimal) -> str:
    return format(_manual_decimal(value, "price value"), "f")


@dataclass(frozen=True, slots=True)
class ManualSupplierCommercialInput:
    """Explicit VAT-included supplier values copied by the user from the PDF."""

    high_rate_czk_per_kwh: Decimal
    low_rate_czk_per_kwh: Decimal
    supplier_standing_czk_month: Decimal

    def __post_init__(self) -> None:
        _manual_decimal(self.high_rate_czk_per_kwh, "high_rate_czk_per_kwh")
        _manual_decimal(self.low_rate_czk_per_kwh, "low_rate_czk_per_kwh")
        _manual_decimal(
            self.supplier_standing_czk_month,
            "supplier_standing_czk_month",
        )

    @property
    def includes_vat(self) -> bool:
        return True

    def as_dict(self) -> dict[str, object]:
        return {
            "high_rate_czk_per_kwh": _decimal_string(self.high_rate_czk_per_kwh),
            "low_rate_czk_per_kwh": _decimal_string(self.low_rate_czk_per_kwh),
            "supplier_standing_czk_month": _decimal_string(
                self.supplier_standing_czk_month
            ),
            "includes_vat": True,
        }


@dataclass(frozen=True, slots=True)
class ManualAllInTariffPreview:
    """Complete all-in preview whose commercial values are explicitly manual."""

    assembly: AllInTariffAssembly
    manual_commercial: ManualSupplierCommercialInput
    supplier_document_sha256: str
    supplier_source_url: str
    regulated_source_url: str
    regulated_checksum: str | None
    validation_reasons: tuple[str, ...]
    authority_method: AllInTariffAuthorityMethod = field(
        default=AllInTariffAuthorityMethod.MANUAL_USER_ENTRY,
        init=False,
    )
    parsing_performed: bool = field(default=False, init=False)
    persistence_performed: bool = field(default=False, init=False)
    activation_performed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.assembly, AllInTariffAssembly):
            raise ValueError("assembly must be AllInTariffAssembly")
        if not isinstance(self.manual_commercial, ManualSupplierCommercialInput):
            raise ValueError("manual_commercial must be ManualSupplierCommercialInput")
        if not isinstance(self.supplier_document_sha256, str) or not _SHA256_RE.fullmatch(
            self.supplier_document_sha256
        ):
            raise ValueError("supplier_document_sha256 must be a SHA-256 digest")
        if not isinstance(self.supplier_source_url, str) or not self.supplier_source_url.strip():
            raise ValueError("supplier_source_url must not be empty")
        if not isinstance(self.regulated_source_url, str) or not self.regulated_source_url.strip():
            raise ValueError("regulated_source_url must not be empty")
        if self.regulated_checksum is not None and not _SHA256_RE.fullmatch(
            self.regulated_checksum
        ):
            raise ValueError("regulated_checksum must be a SHA-256 digest")
        reasons = tuple(self.validation_reasons)
        if not reasons or any(not isinstance(item, str) or not item.strip() for item in reasons):
            raise ValueError("validation_reasons must contain non-empty strings")
        object.__setattr__(self, "validation_reasons", reasons)

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
                _variable_component_dict(item)
                for item in self.assembly.variable_components
            ],
            "fixed_components": [
                _fixed_component_dict(item) for item in self.assembly.fixed_components
            ],
            "manual_supplier_commercial": self.manual_commercial.as_dict(),
            "authority_method": self.authority_method.value,
            "manual_entry": True,
            "supplier_source_url": self.supplier_source_url,
            "supplier_document_sha256": self.supplier_document_sha256,
            "regulated_source_url": self.regulated_source_url,
            "regulated_checksum": self.regulated_checksum,
            "provenance": self.assembly.provenance.as_dict(),
            "validation_reasons": list(self.validation_reasons),
            "all_in_ready": self.assembly.all_in_ready,
            "parsing_performed": self.parsing_performed,
            "persistence_performed": self.persistence_performed,
            "activation_performed": self.activation_performed,
        }


def _variable_component_dict(item: VariablePriceComponent) -> dict[str, object]:
    payload = item.as_dict()
    payload["gross_vt_czk_per_kwh"] = _decimal_string(
        item.gross_high_rate_czk_per_kwh
    )
    payload["gross_nt_czk_per_kwh"] = _decimal_string(
        item.gross_low_rate_czk_per_kwh
    )
    return payload


def _fixed_component_dict(item: FixedPriceComponent) -> dict[str, object]:
    payload = item.as_dict()
    payload["gross_monthly_czk"] = _decimal_string(item.gross_monthly_czk)
    return payload


def _supplier_document_name(download: ValidatedTariffDownload) -> str:
    path = PurePosixPath(urlparse(download.document.source_url).path)
    return path.name or "official-supplier-price-list"


def _supplier_identity(supplier: str) -> tuple[str, str, tuple[str, ...]]:
    try:
        return _SUPPLIER_IDENTITIES[supplier]
    except KeyError as err:
        raise LookupError(
            f"manual commercial preview is not implemented for supplier: {supplier}"
        ) from err


def _host_matches_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _validate_official_supplier_url(supplier: str, source_url: str) -> None:
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
    if not any(_host_matches_domain(host, domain) for domain in domains):
        raise ValueError("supplier source URL is not on the supplier's official domain")


def _validate_regulated_evidence(
    regulated: RegulatedTariffBundle,
    evidence: tuple[PriceEvidence, ...],
) -> None:
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


def build_manual_all_in_tariff_preview(
    *,
    download: ValidatedTariffDownload,
    manual_commercial: ManualSupplierCommercialInput,
    contract: ElectricityContract,
    regulated: RegulatedTariffBundle,
    regulated_evidence: tuple[PriceEvidence, ...],
) -> ManualAllInTariffPreview:
    """Build all-in pricing while keeping manual commercial values explicit."""

    if not isinstance(download, ValidatedTariffDownload):
        raise ValueError("download must be ValidatedTariffDownload")
    if not isinstance(manual_commercial, ManualSupplierCommercialInput):
        raise ValueError("manual_commercial must be ManualSupplierCommercialInput")
    if not isinstance(contract, ElectricityContract):
        raise ValueError("contract must be ElectricityContract")
    if not isinstance(regulated, RegulatedTariffBundle):
        raise ValueError("regulated must be RegulatedTariffBundle")
    if download.persistence_performed or download.activation_performed:
        raise ValueError("manual preview cannot consume an activated download")
    if not regulated.confirmed:
        raise ValueError("regulated tariff bundle must be confirmed for manual preview")

    candidate = download.candidate
    expected_fingerprint = tariff_candidate_selection_fingerprint(candidate)
    if download.selected_fingerprint != expected_fingerprint:
        raise ValueError("selected fingerprint does not match tariff candidate")
    if candidate.match_score != 100:
        raise ValueError("manual preview requires an exact 100-score supplier candidate")
    if candidate.price_scope != PRICE_SCOPE_SUPPLIER_COMMERCIAL:
        raise ValueError("manual preview requires a supplier-commercial candidate")
    if candidate.document.supplier != contract.supplier.value:
        raise ValueError("selected supplier document does not match contract supplier")
    if candidate.product_name.strip() != contract.product_name.strip():
        raise ValueError("selected supplier product does not match contract product")
    if download.document.supplier != candidate.document.supplier:
        raise ValueError("validated supplier document identity does not match candidate")
    if download.document.source_url != candidate.document.source_url:
        raise ValueError("validated supplier source URL does not match candidate")
    if not isinstance(download.document.sha256, str) or not _SHA256_RE.fullmatch(
        download.document.sha256
    ):
        raise ValueError("validated supplier document must have SHA-256")
    if candidate.document.sha256 is not None and (
        candidate.document.sha256 != download.document.sha256
    ):
        raise ValueError("validated supplier SHA-256 does not match pinned candidate")
    _validate_official_supplier_url(contract.supplier.value, download.document.source_url)

    if regulated.distributor != contract.distributor.value:
        raise ValueError("regulated distributor does not match customer contract")
    if regulated.distribution_tariff != contract.distribution_tariff:
        raise ValueError("regulated distribution tariff does not match customer contract")
    if regulated.breaker_code != contract.breaker.code:
        raise ValueError("regulated breaker does not match customer contract")

    display_name, supplier_source_name, _domains = _supplier_identity(
        contract.supplier.value
    )
    evidence = tuple(regulated_evidence)
    _validate_regulated_evidence(regulated, evidence)

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
        high_rate_czk_per_kwh=manual_commercial.high_rate_czk_per_kwh,
        low_rate_czk_per_kwh=manual_commercial.low_rate_czk_per_kwh,
        includes_vat=True,
    )
    supplier_fixed = FixedPriceComponent(
        kind=PriceComponentKind.SUPPLIER_FIXED,
        name=f"{display_name} – ručně potvrzená stálá platba dodavatele",
        monthly_czk=manual_commercial.supplier_standing_czk_month,
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
    return ManualAllInTariffPreview(
        assembly=assembly,
        manual_commercial=manual_commercial,
        supplier_document_sha256=download.document.sha256,
        supplier_source_url=download.document.source_url,
        regulated_source_url=regulated.source_url,
        regulated_checksum=regulated.checksum,
        validation_reasons=(
            "validated supplier-commercial PDF and exact 100-score candidate fingerprint",
            "supplier source revalidated on its official HTTPS domain",
            "supplier document pinned by SHA-256",
            "manual supplier-commercial values explicitly marked as user entry",
            "confirmed regulated tariff bundle",
            "exact customer distributor, distribution tariff and breaker",
            "supplier and regulated provenance linked to official sources",
            "commercial and regulated validity periods overlap",
        ),
    )
