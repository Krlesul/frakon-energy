from datetime import date, datetime, timezone
import hashlib
import importlib.util
from pathlib import Path
import sys
import types

import pytest


PRE_TEXT = """
PRE PROUD NEFIX
Ceník elektřiny pro domácnosti platný od 1. 1. 2026
na distribučním území PREdistribuce, a. s.
Distribuční sazba
D01d, D02d D25d, D26d D27d D35d D45d D56d D57d D61d
Cena za spotřebovanou elektřinu
ve vysokém tarifu [Kč/MWh]
v nízkém tarifu [Kč/MWh]
Měsíční plat za odběrné místo
[Kč/měsíc]
4 356,00
(3 600,00)
4 235,00
(3 500,00)
4 235,00
(3 500,00)
4 356,00
(3 600,00)
4 598,00
(3 800,00)
4 598,00
(3 800,00)
4 598,00
(3 800,00)
4 235,00
(3 500,00)
— 3 993,00
(3 300,00)
3 993,00
(3 300,00)
4 114,00
(3 400,00)
4 235,00
(3 500,00)
4 235,00
(3 500,00)
4 235,00
(3 500,00)
3 993,00
(3 300,00)
143,99
(119,00)
DISTRIBUČNÍ SAZBA
CENA ZA DODÁVKU ELEKTŘINY
CENA ZA DISTRIBUOVANÉ MNOŽSTVÍ ELEKTŘINY
99 999,00
(88 888,00)
Ceny uvedené tučně jsou včetně DPH ve výši 21 %, ceny uvedené v závorkách jsou bez DPH.
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
        "custom_components.frakon_energy.providers.pre_tariffs",
        "custom_components.frakon_energy.providers.pre_tariff_parser",
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
        "custom_components.frakon_energy.providers.pre_tariffs",
        "custom_components/frakon_energy/providers/pre_tariffs.py",
    )
    _load(
        "custom_components.frakon_energy.providers.pre_tariff_parser",
        "custom_components/frakon_energy/providers/pre_tariff_parser.py",
    )
    preview = _load(
        "custom_components.frakon_energy.tariff_parser_preview",
        "custom_components/frakon_energy/tariff_parser_preview.py",
    )
    return contracts, sources, selection, download, pdf_text, preview


def _inputs(*, contract_product: str = "PROUD NEFIX", candidate_valid_from=date(2026, 1, 1)):
    contracts, sources, selection, download_module, pdf_text, preview = load_modules()
    contract = contracts.ElectricityContract(
        supplier=contracts.Supplier.PRE,
        distributor=contracts.Distributor.PRE_DISTRIBUCE,
        product_name=contract_product,
        contract_kind=contracts.ContractKind.INDEFINITE,
        distribution_tariff="D25d",
        breaker=contracts.Breaker(phases=3, amperes=25),
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        customer_confirmed=False,
    )
    candidate = sources.TariffDocumentCandidate(
        document=sources.OfficialTariffDocument(
            supplier="pre",
            source_url=(
                "https://www.pre.cz/cs/linky/dokumenty-ke-stazeni/cenik/elektrina/"
                "pre/moo/pre-proud-nefix/"
            ),
            discovered_at=datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc),
            document_date=candidate_valid_from,
            content_type="application/pdf",
        ),
        product_name="PRE PROUD NEFIX",
        valid_from=candidate_valid_from,
        match_score=100,
        match_reasons=("exact PRE fixture",),
        price_scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    )
    content = b"%PDF-1.7\nPRE parser preview fixture\n%%EOF"
    sha = hashlib.sha256(content).hexdigest()
    document = sources.OfficialTariffDocument(
        supplier="pre",
        source_url=candidate.document.source_url,
        discovered_at=candidate.document.discovered_at,
        document_date=candidate.document.document_date,
        sha256=sha,
        content_type="application/pdf",
    )
    validated = download_module.ValidatedTariffDownload(
        selected_fingerprint=selection.tariff_candidate_selection_fingerprint(candidate),
        candidate=candidate,
        document=document,
        content=content,
        validated_at=datetime(2026, 8, 15, 8, 1, tzinfo=timezone.utc),
    )
    extracted = pdf_text.ExtractedTariffPdfText(
        source_url=document.source_url,
        document_sha256=sha,
        page_count=2,
        text=PRE_TEXT,
    )
    return contract, candidate, validated, extracted, preview


def test_pre_preview_returns_exact_supplier_commercial_prices_without_authority() -> None:
    contract, candidate, validated, extracted, preview = _inputs()

    result = preview.parse_supplier_tariff_preview(validated, extracted, contract)

    assert result.supplier == "pre"
    assert result.product_name == candidate.product_name == "PRE PROUD NEFIX"
    assert result.valid_from == date(2026, 1, 1)
    assert result.distribution_tariff == "D25d"
    assert str(result.high_rate_czk_per_kwh) == "4.235"
    assert str(result.low_rate_czk_per_kwh) == "3.993"
    assert str(result.supplier_standing_czk_month) == "143.99"
    assert result.parser_name == "pre_commercial_v1"
    assert result.extraction_confidence == 100
    assert result.persistence_performed is False
    assert result.activation_performed is False
    assert result.validation_reasons[-1] == (
        "regulated rows in the supplier PDF were excluded from parsing authority"
    )


def test_pre_preview_accepts_only_verified_catalog_alias_identity() -> None:
    contract, _candidate, validated, extracted, preview = _inputs(
        contract_product="PROUD NEFIX"
    )
    assert preview.parse_supplier_tariff_preview(validated, extracted, contract).supplier == "pre"

    drifted, _candidate, validated, extracted, preview = _inputs(
        contract_product="PRE PROUD FAVORIT 2"
    )
    with pytest.raises(ValueError, match="selected PRE product"):
        preview.parse_supplier_tariff_preview(validated, extracted, drifted)


def test_pre_preview_rejects_mutable_source_validity_drift() -> None:
    contract, _candidate, validated, extracted, preview = _inputs(
        candidate_valid_from=date(2026, 2, 1)
    )
    with pytest.raises(ValueError, match="immutable selected candidate"):
        preview.parse_supplier_tariff_preview(validated, extracted, contract)
