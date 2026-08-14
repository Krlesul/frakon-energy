from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import importlib.util
from pathlib import Path
import sys
import types

import pytest


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_modules():
    names = (
        "custom_components", "custom_components.frakon_energy", "custom_components.frakon_energy.providers",
        "custom_components.frakon_energy.contracts", "custom_components.frakon_energy.pricing",
        "custom_components.frakon_energy.tariff_sources", "custom_components.frakon_energy.regulated_pricing",
        "custom_components.frakon_energy.tariff_candidate_selection", "custom_components.frakon_energy.tariff_download",
        "custom_components.frakon_energy.tariff_pdf_text", "custom_components.frakon_energy.tariff_provenance",
        "custom_components.frakon_energy.tariff_assembly", "custom_components.frakon_energy.providers.cez_tariffs",
        "custom_components.frakon_energy.providers.cez_tariff_parser", "custom_components.frakon_energy.tariff_parser_preview",
        "custom_components.frakon_energy.tariff_all_in_preview",
    )
    for name in names:
        sys.modules.pop(name, None)
    for name in ("custom_components", "custom_components.frakon_energy", "custom_components.frakon_energy.providers"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    contracts = _load("custom_components.frakon_energy.contracts", "custom_components/frakon_energy/contracts.py")
    pricing = _load("custom_components.frakon_energy.pricing", "custom_components/frakon_energy/pricing.py")
    sources = _load("custom_components.frakon_energy.tariff_sources", "custom_components/frakon_energy/tariff_sources.py")
    regulated_pricing = _load("custom_components.frakon_energy.regulated_pricing", "custom_components/frakon_energy/regulated_pricing.py")
    selection = _load("custom_components.frakon_energy.tariff_candidate_selection", "custom_components/frakon_energy/tariff_candidate_selection.py")
    download = _load("custom_components.frakon_energy.tariff_download", "custom_components/frakon_energy/tariff_download.py")
    _load("custom_components.frakon_energy.tariff_pdf_text", "custom_components/frakon_energy/tariff_pdf_text.py")
    provenance = _load("custom_components.frakon_energy.tariff_provenance", "custom_components/frakon_energy/tariff_provenance.py")
    _load("custom_components.frakon_energy.tariff_assembly", "custom_components/frakon_energy/tariff_assembly.py")
    _load("custom_components.frakon_energy.providers.cez_tariffs", "custom_components/frakon_energy/providers/cez_tariffs.py")
    _load("custom_components.frakon_energy.providers.cez_tariff_parser", "custom_components/frakon_energy/providers/cez_tariff_parser.py")
    parser_preview = _load("custom_components.frakon_energy.tariff_parser_preview", "custom_components/frakon_energy/tariff_parser_preview.py")
    all_in = _load("custom_components.frakon_energy.tariff_all_in_preview", "custom_components/frakon_energy/tariff_all_in_preview.py")
    return contracts, pricing, regulated_pricing, sources, selection, download, provenance, parser_preview, all_in


def _fixture(*, confirmed=True, breaker="3x25A", regulated_valid_from=date(2026, 1, 1), regulated_valid_to=date(2026, 12, 31)):
    contracts, pricing, regulated_pricing, sources, selection, download_module, provenance, parser_preview, all_in = load_modules()
    contract = contracts.ElectricityContract(
        supplier=contracts.Supplier.CEZ,
        distributor=contracts.Distributor.CEZ_DISTRIBUCE,
        product_name="Basic",
        contract_kind=contracts.ContractKind.INDEFINITE,
        distribution_tariff="D25d",
        breaker=contracts.Breaker(phases=3, amperes=25),
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        customer_confirmed=False,
    )
    candidate = sources.TariffDocumentCandidate(
        document=sources.OfficialTariffDocument(
            supplier="cez",
            source_url="https://www.cez.cz/file/basic.pdf",
            discovered_at=datetime(2026, 8, 14, 16, 45, tzinfo=timezone.utc),
            document_date=date(2026, 1, 1),
            content_type="application/pdf",
        ),
        product_name="Basic",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        match_score=100,
        match_reasons=("exact verified fixture",),
        price_scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    )
    content = b"%PDF-1.7\nall-in preview fixture\n%%EOF"
    sha256 = hashlib.sha256(content).hexdigest()
    document = sources.OfficialTariffDocument(
        supplier="cez",
        source_url=candidate.document.source_url,
        discovered_at=candidate.document.discovered_at,
        document_date=candidate.document.document_date,
        sha256=sha256,
        content_type="application/pdf",
    )
    validated = download_module.ValidatedTariffDownload(
        selected_fingerprint=selection.tariff_candidate_selection_fingerprint(candidate),
        candidate=candidate,
        document=document,
        content=content,
        validated_at=datetime(2026, 8, 14, 16, 46, tzinfo=timezone.utc),
    )
    parsed = parser_preview.SupplierTariffParsePreview(
        supplier="cez",
        product_name="Basic",
        valid_from=date(2026, 1, 1),
        distribution_tariff="D25d",
        high_rate_czk_per_kwh=Decimal("3.96"),
        low_rate_czk_per_kwh=Decimal("3.70"),
        supplier_standing_czk_month=Decimal("130.68"),
        includes_vat=True,
        source_url=document.source_url,
        document_sha256=sha256,
        page_count=2,
        parser_name="cez_commercial_v1",
        extraction_method="pypdf_layout",
        extraction_confidence=100,
        validation_reasons=("exact fixture validation",),
    )
    regulated_checksum = "b" * 64
    regulated_url = "https://eru.gov.cz/energeticky-regulacni-vestnik-182025"
    regulated = regulated_pricing.RegulatedTariffBundle(
        distributor="cez_distribuce",
        distribution_tariff="D25d",
        breaker_code=breaker,
        valid_from=regulated_valid_from,
        valid_to=regulated_valid_to,
        variable_components=(
            pricing.VariablePriceComponent(kind=pricing.PriceComponentKind.DISTRIBUTION, name="Regulovaná distribuce", high_rate_czk_per_kwh=Decimal("1.00"), low_rate_czk_per_kwh=Decimal("0.50"), includes_vat=False),
            pricing.VariablePriceComponent(kind=pricing.PriceComponentKind.SYSTEM_SERVICES, name="Systémové služby", high_rate_czk_per_kwh=Decimal("0.10"), low_rate_czk_per_kwh=Decimal("0.10"), includes_vat=False),
            pricing.VariablePriceComponent(kind=pricing.PriceComponentKind.POZE, name="POZE", high_rate_czk_per_kwh=Decimal("0"), low_rate_czk_per_kwh=Decimal("0"), includes_vat=False),
            pricing.VariablePriceComponent(kind=pricing.PriceComponentKind.ELECTRICITY_TAX, name="Daň z elektřiny", high_rate_czk_per_kwh=Decimal("0.0283"), low_rate_czk_per_kwh=Decimal("0.0283"), includes_vat=False),
        ),
        fixed_components=(
            pricing.FixedPriceComponent(kind=pricing.PriceComponentKind.BREAKER_FIXED, name="Plat za příkon podle hlavního jističe", monthly_czk=Decimal("200"), includes_vat=False),
            pricing.FixedPriceComponent(kind=pricing.PriceComponentKind.OTHER_FIXED, name=regulated_pricing.NON_NETWORK_INFRASTRUCTURE_COMPONENT_NAME, monthly_czk=Decimal("12.87"), includes_vat=False),
        ),
        source_url=regulated_url,
        document_date=date(2025, 11, 28),
        checksum=regulated_checksum,
        confirmed=confirmed,
    )
    evidence = (
        provenance.PriceEvidence(
            scope=sources.PRICE_SCOPE_REGULATED,
            source_name="Energetický regulační úřad",
            document_name="Cenový výměr 14/2025 – fixture",
            source_url=regulated_url,
            valid_from=regulated_valid_from,
            valid_to=regulated_valid_to,
            document_date=date(2025, 11, 28),
            checksum=regulated_checksum,
        ),
    )
    return sources, provenance, all_in, contract, validated, parsed, regulated, evidence


def test_all_in_preview_combines_gross_supplier_and_confirmed_regulated_prices() -> None:
    _sources, _provenance, all_in, contract, validated, parsed, regulated, evidence = _fixture()
    result = all_in.build_all_in_tariff_preview(download=validated, parsed=parsed, contract=contract, regulated=regulated, regulated_evidence=evidence)
    assert str(result.assembly.all_in_vt_czk_kwh) == "5.325243"
    assert str(result.assembly.all_in_nt_czk_kwh) == "4.460243"
    assert str(result.assembly.fixed_monthly_total_czk) == "388.2527"
    payload = result.as_dict()
    assert payload["all_in_vt_czk_kwh"] == "5.325243"
    assert payload["all_in_nt_czk_kwh"] == "4.460243"
    assert payload["fixed_monthly_total_czk"] == "388.2527"
    assert payload["all_in_ready"] is True
    assert payload["persistence_performed"] is False
    assert payload["activation_performed"] is False
    assert len(payload["variable_components"]) == 5
    assert len(payload["fixed_components"]) == 3
    assert payload["variable_components"][1]["gross_vt_czk_per_kwh"] == "1.2100"
    assert payload["fixed_components"][1]["gross_monthly_czk"] == "242.00"
    assert len(payload["provenance"]["evidence"]) == 2


def test_unconfirmed_regulated_bundle_cannot_produce_all_in_preview() -> None:
    _sources, _provenance, all_in, contract, validated, parsed, regulated, evidence = _fixture(confirmed=False)
    with pytest.raises(ValueError, match="must be confirmed"):
        all_in.build_all_in_tariff_preview(download=validated, parsed=parsed, contract=contract, regulated=regulated, regulated_evidence=evidence)


def test_breaker_mismatch_fails_closed() -> None:
    _sources, _provenance, all_in, contract, validated, parsed, regulated, evidence = _fixture(breaker="3x32A")
    with pytest.raises(ValueError, match="breaker"):
        all_in.build_all_in_tariff_preview(download=validated, parsed=parsed, contract=contract, regulated=regulated, regulated_evidence=evidence)


def test_regulated_evidence_must_match_bundle_source_and_checksum() -> None:
    sources, provenance, all_in, contract, validated, parsed, regulated, _evidence = _fixture()
    wrong_source = (provenance.PriceEvidence(scope=sources.PRICE_SCOPE_REGULATED, source_name="ERÚ", document_name="wrong source", source_url="https://eru.gov.cz/other-official-page", valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31), checksum="b" * 64),)
    with pytest.raises(ValueError, match="does not contain"):
        all_in.build_all_in_tariff_preview(download=validated, parsed=parsed, contract=contract, regulated=regulated, regulated_evidence=wrong_source)
    wrong_checksum = (provenance.PriceEvidence(scope=sources.PRICE_SCOPE_REGULATED, source_name="ERÚ", document_name="wrong checksum", source_url=regulated.source_url, valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31), checksum="c" * 64),)
    with pytest.raises(ValueError, match="checksum"):
        all_in.build_all_in_tariff_preview(download=validated, parsed=parsed, contract=contract, regulated=regulated, regulated_evidence=wrong_checksum)


def test_supplier_sha_drift_is_rejected_before_assembly() -> None:
    _sources, _provenance, all_in, contract, validated, parsed, regulated, evidence = _fixture()
    parsed_drift = type(parsed)(
        supplier=parsed.supplier, product_name=parsed.product_name, valid_from=parsed.valid_from,
        distribution_tariff=parsed.distribution_tariff, high_rate_czk_per_kwh=parsed.high_rate_czk_per_kwh,
        low_rate_czk_per_kwh=parsed.low_rate_czk_per_kwh, supplier_standing_czk_month=parsed.supplier_standing_czk_month,
        includes_vat=parsed.includes_vat, source_url=parsed.source_url, document_sha256="f" * 64,
        page_count=parsed.page_count, parser_name=parsed.parser_name, extraction_method=parsed.extraction_method,
        extraction_confidence=parsed.extraction_confidence, validation_reasons=parsed.validation_reasons,
    )
    with pytest.raises(ValueError, match="SHA-256"):
        all_in.build_all_in_tariff_preview(download=validated, parsed=parsed_drift, contract=contract, regulated=regulated, regulated_evidence=evidence)


def test_non_overlapping_regulated_validity_fails_closed() -> None:
    _sources, _provenance, all_in, contract, validated, parsed, regulated, evidence = _fixture(regulated_valid_from=date(2025, 1, 1), regulated_valid_to=date(2025, 12, 31))
    with pytest.raises(ValueError, match="do not overlap"):
        all_in.build_all_in_tariff_preview(download=validated, parsed=parsed, contract=contract, regulated=regulated, regulated_evidence=evidence)
