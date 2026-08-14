from datetime import date, datetime, timezone
import hashlib
import importlib.util
from pathlib import Path
import sys
import types

import pytest


BASIC_2026_TEXT = """
Ceník elektřiny pro domácnosti
Basic
Smlouva na dobu neurčitou
Účinnost obchodních cen od 1. 1. 2026
Distribuční sazba D01d D02d D25d D26d D27d D35d D45d D56d D57d D61d
Vysoký tarif Kč/MWh
Nízký tarif Kč/MWh
Stálá platba Kč/měsíc
Uvádíme jen obchodní (neregulovanou) část ceny.
Tučně uvedené ceny jsou s 21% DPH, v závorce bez DPH.
3 860,00 3 860,00 3 960,00 3 960,00 3 960,00 4 140,00 4 140,00 4 140,00 4 140,00 3 860,00
(3 190,08) (3 190,08) (3 272,73) (3 272,73) (3 272,73) (3 421,49) (3 421,49) (3 421,49) (3 421,49) (3 190,08)
– – 3 700,00 3 700,00 3 650,00 4 020,00 4 020,00 4 020,00 4 020,00 3 860,00
(3 057,85) (3 057,85) (3 016,53) (3 322,31) (3 322,31) (3 322,31) (3 322,31) (3 190,08)
147,62 147,62 130,68 130,68 130,68 130,68 130,68 130,68 130,68 130,68
(122,00) (122,00) (108,00) (108,00) (108,00) (108,00) (108,00) (108,00) (108,00) (108,00)
"""


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_modules():
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.providers",
        "custom_components.frakon_energy.contracts",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components.frakon_energy.tariff_download",
        "custom_components.frakon_energy.tariff_pdf_text",
        "custom_components.frakon_energy.providers.cez_tariff_parser",
        "custom_components.frakon_energy.tariff_parser_preview",
    )
    for name in names:
        sys.modules.pop(name, None)

    for name in (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.providers",
    ):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    contracts = _load(
        "custom_components.frakon_energy.contracts",
        "custom_components/frakon_energy/contracts.py",
    )
    sources = _load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    selection = _load(
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components/frakon_energy/tariff_candidate_selection.py",
    )
    download = _load(
        "custom_components.frakon_energy.tariff_download",
        "custom_components/frakon_energy/tariff_download.py",
    )
    pdf_text = _load(
        "custom_components.frakon_energy.tariff_pdf_text",
        "custom_components/frakon_energy/tariff_pdf_text.py",
    )
    _load(
        "custom_components.frakon_energy.providers.cez_tariff_parser",
        "custom_components/frakon_energy/providers/cez_tariff_parser.py",
    )
    preview = _load(
        "custom_components.frakon_energy.tariff_parser_preview",
        "custom_components/frakon_energy/tariff_parser_preview.py",
    )
    return contracts, sources, selection, download, pdf_text, preview


def _cez_inputs(
    contracts,
    sources,
    selection,
    download_module,
    pdf_text,
    *,
    text: str = BASIC_2026_TEXT,
    candidate_product: str = "Basic",
    candidate_valid_from: date = date(2026, 1, 1),
    contract_product: str = "Basic",
    tariff: str = "D25d",
):
    contract = contracts.ElectricityContract(
        supplier=contracts.Supplier.CEZ,
        distributor=contracts.Distributor.CEZ_DISTRIBUCE,
        product_name=contract_product,
        contract_kind=contracts.ContractKind.INDEFINITE,
        distribution_tariff=tariff,
        breaker=contracts.Breaker(phases=3, amperes=25),
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        customer_confirmed=False,
    )
    candidate = sources.TariffDocumentCandidate(
        document=sources.OfficialTariffDocument(
            supplier="cez",
            source_url="https://www.cez.cz/file/basic.pdf",
            discovered_at=datetime(2026, 8, 14, 15, 45, tzinfo=timezone.utc),
            document_date=candidate_valid_from,
            content_type="application/pdf",
        ),
        product_name=candidate_product,
        valid_from=candidate_valid_from,
        match_score=100,
        match_reasons=("exact verified fixture",),
        price_scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    )
    content = b"%PDF-1.7\nparser preview fixture\n%%EOF"
    document = sources.OfficialTariffDocument(
        supplier="cez",
        source_url=candidate.document.source_url,
        discovered_at=candidate.document.discovered_at,
        document_date=candidate.document.document_date,
        sha256=hashlib.sha256(content).hexdigest(),
        content_type="application/pdf",
    )
    selected_fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)
    validated = download_module.ValidatedTariffDownload(
        selected_fingerprint=selected_fingerprint,
        candidate=candidate,
        document=document,
        content=content,
        validated_at=datetime(2026, 8, 14, 15, 46, tzinfo=timezone.utc),
    )
    extracted = pdf_text.ExtractedTariffPdfText(
        source_url=document.source_url,
        document_sha256=document.sha256,
        page_count=2,
        text=text,
    )
    return contract, candidate, validated, extracted


def test_cez_preview_returns_exact_validated_supplier_prices_without_authority() -> None:
    contracts, sources, selection, download_module, pdf_text, preview = load_modules()
    contract, _candidate, validated, extracted = _cez_inputs(
        contracts, sources, selection, download_module, pdf_text
    )

    result = preview.parse_supplier_tariff_preview(validated, extracted, contract)

    assert result.supplier == "cez"
    assert result.product_name == "Basic"
    assert result.valid_from == date(2026, 1, 1)
    assert result.distribution_tariff == "D25d"
    assert str(result.high_rate_czk_per_kwh) == "3.96"
    assert str(result.low_rate_czk_per_kwh) == "3.7"
    assert str(result.supplier_standing_czk_month) == "130.68"
    assert result.includes_vat is True
    assert result.document_sha256 == validated.document.sha256
    assert result.page_count == 2
    assert result.parser_name == "cez_commercial_v1"
    assert result.extraction_method == "pypdf_layout"
    assert result.extraction_confidence == 100
    assert result.price_scope == sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL
    assert result.parsing_performed is True
    assert result.persistence_performed is False
    assert result.activation_performed is False

    payload = result.as_dict()
    assert payload["high_rate_czk_per_kwh"] == "3.96"
    assert payload["low_rate_czk_per_kwh"] == "3.7"
    assert payload["supplier_standing_czk_month"] == "130.68"
    assert payload["validation_reasons"] == [
        "validated selected supplier-commercial PDF",
        "exact document source URL and SHA-256 match",
        "exact ČEZ product match",
        "exact distribution tariff match",
        "exact commercial-price validity match",
    ]


def test_preview_rejects_parsed_product_drift() -> None:
    contracts, sources, selection, download_module, pdf_text, preview = load_modules()
    drifted_text = BASIC_2026_TEXT.replace("\nBasic\n", "\neTarif\n", 1)
    contract, _candidate, validated, extracted = _cez_inputs(
        contracts,
        sources,
        selection,
        download_module,
        pdf_text,
        text=drifted_text,
    )

    with pytest.raises(ValueError, match="parsed ČEZ product"):
        preview.parse_supplier_tariff_preview(validated, extracted, contract)


def test_preview_rejects_candidate_contract_product_drift() -> None:
    contracts, sources, selection, download_module, pdf_text, preview = load_modules()
    contract, _candidate, validated, extracted = _cez_inputs(
        contracts,
        sources,
        selection,
        download_module,
        pdf_text,
        contract_product="eTarif",
    )

    with pytest.raises(ValueError, match="selected ČEZ product"):
        preview.parse_supplier_tariff_preview(validated, extracted, contract)


def test_preview_rejects_parsed_validity_drift() -> None:
    contracts, sources, selection, download_module, pdf_text, preview = load_modules()
    contract, _candidate, validated, extracted = _cez_inputs(
        contracts,
        sources,
        selection,
        download_module,
        pdf_text,
        candidate_valid_from=date(2026, 2, 1),
    )

    with pytest.raises(ValueError, match="validity"):
        preview.parse_supplier_tariff_preview(validated, extracted, contract)


def test_preview_rejects_extracted_source_or_sha_drift_before_parsing() -> None:
    contracts, sources, selection, download_module, pdf_text, preview = load_modules()
    contract, _candidate, validated, extracted = _cez_inputs(
        contracts, sources, selection, download_module, pdf_text
    )

    wrong_source = pdf_text.ExtractedTariffPdfText(
        source_url="https://www.cez.cz/file/other.pdf",
        document_sha256=extracted.document_sha256,
        page_count=extracted.page_count,
        text=extracted.text,
    )
    with pytest.raises(ValueError, match="source URL"):
        preview.parse_supplier_tariff_preview(validated, wrong_source, contract)

    wrong_sha = pdf_text.ExtractedTariffPdfText(
        source_url=extracted.source_url,
        document_sha256="f" * 64,
        page_count=extracted.page_count,
        text=extracted.text,
    )
    with pytest.raises(ValueError, match="SHA-256"):
        preview.parse_supplier_tariff_preview(validated, wrong_sha, contract)


def test_unsupported_supplier_fails_explicitly_instead_of_guessing_parser() -> None:
    contracts, sources, selection, download_module, pdf_text, preview = load_modules()
    contract = contracts.ElectricityContract(
        supplier=contracts.Supplier.EON,
        distributor=contracts.Distributor.EG_D,
        product_name="Variant PRO na 2 roky",
        contract_kind=contracts.ContractKind.FIXED,
        distribution_tariff="D25d",
        breaker=contracts.Breaker(phases=3, amperes=25),
        valid_from=date(2026, 3, 30),
        fixation_end=date(2028, 3, 29),
        customer_confirmed=False,
    )
    candidate = sources.TariffDocumentCandidate(
        document=sources.OfficialTariffDocument(
            supplier="eon",
            source_url="https://www.eon.cz/getmedia/fixture.pdf",
            discovered_at=datetime(2026, 8, 14, 16, 0, tzinfo=timezone.utc),
            document_date=date(2026, 3, 30),
            content_type="application/pdf",
        ),
        product_name=contract.product_name,
        valid_from=date(2026, 3, 30),
        match_score=100,
        match_reasons=("fixture",),
        price_scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    )
    content = b"%PDF-1.7\nunsupported parser fixture\n%%EOF"
    document = sources.OfficialTariffDocument(
        supplier="eon",
        source_url=candidate.document.source_url,
        discovered_at=candidate.document.discovered_at,
        document_date=candidate.document.document_date,
        sha256=hashlib.sha256(content).hexdigest(),
        content_type="application/pdf",
    )
    validated = download_module.ValidatedTariffDownload(
        selected_fingerprint=selection.tariff_candidate_selection_fingerprint(candidate),
        candidate=candidate,
        document=document,
        content=content,
        validated_at=datetime(2026, 8, 14, 16, 1, tzinfo=timezone.utc),
    )
    extracted = pdf_text.ExtractedTariffPdfText(
        source_url=document.source_url,
        document_sha256=document.sha256,
        page_count=1,
        text="E.ON fixture text",
    )

    with pytest.raises(LookupError, match="not implemented: eon"):
        preview.parse_supplier_tariff_preview(validated, extracted, contract)
