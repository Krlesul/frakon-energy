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

EON_VARIANT_TEXT = """
Elektřina
Ceník Variant PRO na 2 roky 3/26
Produktová řada Variant PRO na 2 roky
Obchodní cena za elektřinu platná od 30. 3. 2026
Obchodní cena za dodávku elektřiny
Tučně uvedené ceny jsou včetně 21% DPH.
Běžná spotřeba D01d, D02d
3 3322 754
–
–
168
139
Ohřev vody, akumulační vytápění, elektromobil D25d, D26d, D27d
3 3202 744
2 9872 469
168
139
Hybridní vytápění D35d
3 3202 744
2 9872 469
168
139
Přímotopné vytápění, tepelné čerpadlo D45d, D56d, D57d
3 3202 744
2 9872 469
168
139
Víkendová spotřeba D61d
3 3202 744
2 9872 469
168
139
Celková cena elektřiny zahrnuje obchodní cenu za dodávku elektřiny a cenu za související služby.
Regulovaná cena za související služby v elektroenergetice
D25d, D26d, D27d
99 999
88 888
77 777
66 666
55 555
44 444
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
        "custom_components.frakon_energy.providers.cez_tariffs",
        "custom_components.frakon_energy.providers.cez_tariff_parser",
        "custom_components.frakon_energy.providers.eon_tariffs",
        "custom_components.frakon_energy.providers.eon_tariff_parser",
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
        "custom_components.frakon_energy.providers.cez_tariffs",
        "custom_components/frakon_energy/providers/cez_tariffs.py",
    )
    _load(
        "custom_components.frakon_energy.providers.cez_tariff_parser",
        "custom_components/frakon_energy/providers/cez_tariff_parser.py",
    )
    _load(
        "custom_components.frakon_energy.providers.eon_tariffs",
        "custom_components/frakon_energy/providers/eon_tariffs.py",
    )
    _load(
        "custom_components.frakon_energy.providers.eon_tariff_parser",
        "custom_components/frakon_energy/providers/eon_tariff_parser.py",
    )
    preview = _load(
        "custom_components.frakon_energy.tariff_parser_preview",
        "custom_components/frakon_energy/tariff_parser_preview.py",
    )
    return contracts, sources, selection, download, pdf_text, preview


def _validated_inputs(
    sources,
    selection,
    download_module,
    pdf_text,
    *,
    supplier: str,
    source_url: str,
    product_name: str,
    valid_from: date,
    valid_to: date | None,
    text: str,
    page_count: int = 2,
):
    candidate = sources.TariffDocumentCandidate(
        document=sources.OfficialTariffDocument(
            supplier=supplier,
            source_url=source_url,
            discovered_at=datetime(2026, 8, 14, 15, 45, tzinfo=timezone.utc),
            document_date=valid_from,
            content_type="application/pdf",
        ),
        product_name=product_name,
        valid_from=valid_from,
        valid_to=valid_to,
        match_score=100,
        match_reasons=("exact verified fixture",),
        price_scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    )
    content = b"%PDF-1.7\nparser preview fixture\n%%EOF"
    document = sources.OfficialTariffDocument(
        supplier=supplier,
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
        validated_at=datetime(2026, 8, 14, 15, 46, tzinfo=timezone.utc),
    )
    extracted = pdf_text.ExtractedTariffPdfText(
        source_url=document.source_url,
        document_sha256=document.sha256,
        page_count=page_count,
        text=text,
    )
    return candidate, validated, extracted


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
    candidate, validated, extracted = _validated_inputs(
        sources,
        selection,
        download_module,
        pdf_text,
        supplier="cez",
        source_url="https://www.cez.cz/file/basic.pdf",
        product_name=candidate_product,
        valid_from=candidate_valid_from,
        valid_to=None,
        text=text,
    )
    return contract, candidate, validated, extracted


def _eon_inputs(
    contracts,
    sources,
    selection,
    download_module,
    pdf_text,
    *,
    contract_product: str = "Variant PRO na 2 roky",
    tariff: str = "D25d",
):
    contract = contracts.ElectricityContract(
        supplier=contracts.Supplier.EON,
        distributor=contracts.Distributor.EG_D,
        product_name=contract_product,
        contract_kind=contracts.ContractKind.FIXED,
        distribution_tariff=tariff,
        breaker=contracts.Breaker(phases=3, amperes=25),
        valid_from=date(2026, 3, 30),
        fixation_end=date(2028, 3, 29),
        customer_confirmed=False,
    )
    candidate, validated, extracted = _validated_inputs(
        sources,
        selection,
        download_module,
        pdf_text,
        supplier="eon",
        source_url="https://www.eon.cz/getmedia/fixture.pdf",
        product_name="Variant PRO na 2 roky",
        valid_from=date(2026, 3, 30),
        valid_to=None,
        text=EON_VARIANT_TEXT,
    )
    return contract, candidate, validated, extracted


def test_parser_support_registry_is_explicit() -> None:
    contracts, _sources, _selection, _download, _pdf, preview = load_modules()

    assert preview.supplier_parser_supported(contracts.Supplier.CEZ)
    assert preview.supplier_parser_supported(contracts.Supplier.EON)
    assert preview.supplier_parser_supported(contracts.Supplier.PRE)
    assert not preview.supplier_parser_supported(contracts.Supplier.MND)
    assert preview.supplier_parser_supported("cez")
    assert preview.supplier_parser_supported("eon")
    assert preview.supplier_parser_supported("pre")
    assert not preview.supplier_parser_supported("mnd")


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
    assert str(result.low_rate_czk_per_kwh) == "3.70"
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
    assert payload["low_rate_czk_per_kwh"] == "3.70"
    assert payload["supplier_standing_czk_month"] == "130.68"
    assert payload["validation_reasons"] == [
        "validated selected supplier-commercial PDF",
        "exact document source URL and SHA-256 match",
        "exact ČEZ parsed product matches selected canonical candidate",
        "verified ČEZ contract product or official alias match",
        "exact distribution tariff match",
        "exact commercial-price validity match",
    ]


def test_cez_preview_accepts_plain_pypdf_fallback_provenance() -> None:
    contracts, sources, selection, download_module, pdf_text, preview = load_modules()
    contract, _candidate, validated, extracted = _cez_inputs(
        contracts, sources, selection, download_module, pdf_text
    )
    fallback = pdf_text.ExtractedTariffPdfText(
        source_url=extracted.source_url,
        document_sha256=extracted.document_sha256,
        page_count=extracted.page_count,
        text=extracted.text,
        extraction_method="pypdf_plain_fallback",
    )

    result = preview.parse_supplier_tariff_preview(validated, fallback, contract)

    assert result.extraction_method == "pypdf_plain_fallback"
    assert str(result.high_rate_czk_per_kwh) == "3.96"
    assert str(result.low_rate_czk_per_kwh) == "3.70"


def test_eon_preview_returns_exact_validated_supplier_prices_without_authority() -> None:
    contracts, sources, selection, download_module, pdf_text, preview = load_modules()
    contract, candidate, validated, extracted = _eon_inputs(
        contracts, sources, selection, download_module, pdf_text
    )

    result = preview.parse_supplier_tariff_preview(validated, extracted, contract)

    assert result.supplier == "eon"
    assert result.product_name == candidate.product_name == "Variant PRO na 2 roky"
    assert result.valid_from == candidate.valid_from == date(2026, 3, 30)
    assert result.distribution_tariff == "D25d"
    assert str(result.high_rate_czk_per_kwh) == "3.32"
    assert str(result.low_rate_czk_per_kwh) == "2.987"
    assert str(result.supplier_standing_czk_month) == "168"
    assert result.includes_vat is True
    assert result.document_sha256 == validated.document.sha256
    assert result.parser_name == "eon_commercial_v1"
    assert result.extraction_confidence == 100
    assert result.persistence_performed is False
    assert result.activation_performed is False
    assert result.validation_reasons[-1] == (
        "regulated rows in the supplier PDF were excluded from parsing authority"
    )


def test_cez_preview_accepts_explicit_verified_catalog_alias() -> None:
    contracts, sources, selection, download_module, pdf_text, preview = load_modules()
    contract, _candidate, validated, extracted = _cez_inputs(
        contracts,
        sources,
        selection,
        download_module,
        pdf_text,
        candidate_product="Basic",
        contract_product="Elektřina Basic",
    )

    result = preview.parse_supplier_tariff_preview(validated, extracted, contract)

    assert result.product_name == "Basic"
    assert result.extraction_confidence == 100


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


def test_eon_preview_rejects_candidate_contract_product_drift() -> None:
    contracts, sources, selection, download_module, pdf_text, preview = load_modules()
    contract, _candidate, validated, extracted = _eon_inputs(
        contracts,
        sources,
        selection,
        download_module,
        pdf_text,
        contract_product="Elektřina výhodně PRO na 3 roky",
    )

    with pytest.raises(ValueError, match="selected E.ON product"):
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
        supplier=contracts.Supplier.MND,
        distributor=contracts.Distributor.CEZ_DISTRIBUCE,
        product_name="MND fixture",
        contract_kind=contracts.ContractKind.INDEFINITE,
        distribution_tariff="D25d",
        breaker=contracts.Breaker(phases=3, amperes=25),
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        customer_confirmed=False,
    )
    _candidate, validated, extracted = _validated_inputs(
        sources,
        selection,
        download_module,
        pdf_text,
        supplier="mnd",
        source_url="https://www.mnd.cz/fixture.pdf",
        product_name=contract.product_name,
        valid_from=date(2026, 1, 1),
        valid_to=None,
        text="MND fixture text",
        page_count=1,
    )

    with pytest.raises(LookupError, match="not implemented: mnd"):
        preview.parse_supplier_tariff_preview(validated, extracted, contract)
