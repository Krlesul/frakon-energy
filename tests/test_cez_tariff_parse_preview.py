from datetime import date, datetime, timezone
from decimal import Decimal
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

NOW = datetime(2026, 8, 14, 17, 10, tzinfo=timezone.utc)
DAY = date(2026, 8, 14)
SOURCE_URL = "https://www.cez.cz/file/edee/basic-2026.pdf"
CONTENT = b"%PDF-1.4\nvalidated preview fixture"
SHA = hashlib.sha256(CONTENT).hexdigest()


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
        "custom_components.frakon_energy.contracts",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components.frakon_energy.tariff_download",
        "custom_components.frakon_energy.tariff_pdf_text",
        "custom_components.frakon_energy.providers",
        "custom_components.frakon_energy.providers.cez_tariff_parser",
        "custom_components.frakon_energy.cez_tariff_parse_preview",
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
        "custom_components.frakon_energy.cez_tariff_parse_preview",
        "custom_components/frakon_energy/cez_tariff_parse_preview.py",
    )
    return contracts, sources, selection, download, pdf_text, preview


def _contract(contracts, *, supplier=None, tariff="D25d"):
    return contracts.ElectricityContract(
        supplier=supplier or contracts.Supplier.CEZ,
        distributor=contracts.Distributor.CEZ_DISTRIBUCE,
        product_name="Basic",
        contract_kind=contracts.ContractKind.INDEFINITE,
        distribution_tariff=tariff,
        breaker=contracts.Breaker(phases=3, amperes=25),
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        customer_confirmed=False,
    )


def _download(sources, selection, download, *, product="Basic", valid_from=date(2026, 1, 1), valid_to=None):
    candidate_document = sources.OfficialTariffDocument(
        supplier="cez",
        source_url=SOURCE_URL,
        discovered_at=NOW,
        document_date=date(2026, 1, 1),
        content_type="application/pdf",
    )
    candidate = sources.TariffDocumentCandidate(
        document=candidate_document,
        product_name=product,
        valid_from=valid_from,
        valid_to=valid_to,
        match_score=100,
        match_reasons=("exact verified product",),
        price_scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    )
    fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)
    pinned_document = sources.OfficialTariffDocument(
        supplier="cez",
        source_url=SOURCE_URL,
        discovered_at=NOW,
        document_date=date(2026, 1, 1),
        sha256=SHA,
        content_type="application/pdf",
    )
    return download.ValidatedTariffDownload(
        selected_fingerprint=fingerprint,
        candidate=candidate,
        document=pinned_document,
        content=CONTENT,
        validated_at=NOW,
    )


def _extracted(pdf_text, *, text=BASIC_2026_TEXT, source_url=SOURCE_URL, sha=SHA):
    return pdf_text.ExtractedTariffPdfText(
        source_url=source_url,
        document_sha256=sha,
        page_count=2,
        text=text,
    )


def test_preview_exposes_exact_supplier_values_without_all_in_authority() -> None:
    contracts, sources, selection, download, pdf_text, preview = load_modules()
    selected = _download(sources, selection, download)
    result = preview.preview_cez_commercial_tariff_text(
        _extracted(pdf_text),
        download=selected,
        contract=_contract(contracts),
        day=DAY,
    )

    assert result.product_name == "Basic"
    assert result.distribution_tariff == "D25d"
    assert result.high_rate_czk_per_kwh == Decimal("3.96000")
    assert result.low_rate_czk_per_kwh == Decimal("3.70000")
    assert result.supplier_standing_czk_month == Decimal("130.68")
    assert result.includes_vat is True
    assert result.source_url == SOURCE_URL
    assert result.document_sha256 == SHA
    assert result.price_scope == sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL
    assert result.all_in_ready is False
    assert result.persistence_performed is False
    assert result.activation_performed is False

    payload = result.as_dict()
    assert payload["high_rate_czk_per_kwh"] == "3.96000"
    assert payload["low_rate_czk_per_kwh"] == "3.70000"
    assert payload["supplier_standing_czk_month"] == "130.68"
    assert payload["contract_fingerprint"] == contracts.contract_fingerprint(_contract(contracts))
    assert payload["candidate_fingerprint"] == selection.tariff_candidate_selection_fingerprint(selected.candidate)


def test_single_rate_preview_preserves_missing_nt_instead_of_inventing_value() -> None:
    contracts, sources, selection, download, pdf_text, preview = load_modules()
    result = preview.preview_cez_commercial_tariff_text(
        _extracted(pdf_text),
        download=_download(sources, selection, download),
        contract=_contract(contracts, tariff="D01d"),
        day=DAY,
    )

    assert result.high_rate_czk_per_kwh == Decimal("3.86000")
    assert result.low_rate_czk_per_kwh is None
    assert result.as_dict()["low_rate_czk_per_kwh"] is None
    assert result.all_in_ready is False


def test_parsed_product_must_match_selected_canonical_candidate() -> None:
    contracts, sources, selection, download, pdf_text, preview = load_modules()
    selected = _download(sources, selection, download, product="eTarif")

    with pytest.raises(ValueError, match="product does not match"):
        preview.preview_cez_commercial_tariff_text(
            _extracted(pdf_text),
            download=selected,
            contract=_contract(contracts),
            day=DAY,
        )


def test_parsed_validity_must_match_selected_candidate_version() -> None:
    contracts, sources, selection, download, pdf_text, preview = load_modules()
    selected = _download(
        sources,
        selection,
        download,
        valid_from=date(2026, 7, 1),
    )

    with pytest.raises(ValueError, match="validity does not match"):
        preview.preview_cez_commercial_tariff_text(
            _extracted(pdf_text),
            download=selected,
            contract=_contract(contracts),
            day=DAY,
        )


def test_extracted_source_and_checksum_are_bound_to_validated_download() -> None:
    contracts, sources, selection, download, pdf_text, preview = load_modules()
    selected = _download(sources, selection, download)
    contract = _contract(contracts)

    with pytest.raises(ValueError, match="source does not match"):
        preview.preview_cez_commercial_tariff_text(
            _extracted(pdf_text, source_url="https://www.cez.cz/file/other.pdf"),
            download=selected,
            contract=contract,
            day=DAY,
        )

    with pytest.raises(ValueError, match="checksum does not match"):
        preview.preview_cez_commercial_tariff_text(
            _extracted(pdf_text, sha="a" * 64),
            download=selected,
            contract=contract,
            day=DAY,
        )


def test_non_cez_or_out_of_period_contract_fails_before_price_parse() -> None:
    contracts, sources, selection, download, pdf_text, preview = load_modules()
    selected = _download(sources, selection, download)
    extracted = _extracted(pdf_text)

    with pytest.raises(ValueError, match="only ČEZ contracts"):
        preview.preview_cez_commercial_tariff_text(
            extracted,
            download=selected,
            contract=_contract(contracts, supplier=contracts.Supplier.EON),
            day=DAY,
        )

    with pytest.raises(ValueError, match="contract does not apply"):
        preview.preview_cez_commercial_tariff_text(
            extracted,
            download=selected,
            contract=_contract(contracts),
            day=date(2027, 1, 1),
        )


def test_candidate_must_apply_on_preview_day() -> None:
    contracts, sources, selection, download, pdf_text, preview = load_modules()
    selected = _download(
        sources,
        selection,
        download,
        valid_to=date(2026, 7, 31),
    )

    with pytest.raises(ValueError, match="candidate does not apply"):
        preview.preview_cez_commercial_tariff_text(
            _extracted(pdf_text),
            download=selected,
            contract=_contract(contracts),
            day=DAY,
        )


def test_validated_download_wrapper_uses_bounded_pdf_extractor() -> None:
    contracts, sources, selection, download, pdf_text, preview = load_modules()
    selected = _download(sources, selection, download)
    extracted = _extracted(pdf_text)
    calls = []

    def fake_extract(value):
        calls.append(value)
        return extracted

    preview.extract_validated_tariff_pdf_text = fake_extract
    result = preview.preview_validated_cez_commercial_tariff(
        selected,
        contract=_contract(contracts),
        day=DAY,
    )

    assert calls == [selected]
    assert result.product_name == "Basic"
    assert result.parsing_performed is True
    assert result.activation_performed is False
