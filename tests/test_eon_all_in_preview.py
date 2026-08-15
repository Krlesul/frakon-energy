from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import importlib.util
from pathlib import Path
import sys
import types


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
        "custom_components.frakon_energy.pricing",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components.frakon_energy.tariff_download",
        "custom_components.frakon_energy.tariff_pdf_text",
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components.frakon_energy.tariff_assembly",
        "custom_components.frakon_energy.providers.cez_tariffs",
        "custom_components.frakon_energy.providers.cez_tariff_parser",
        "custom_components.frakon_energy.providers.eon_tariffs",
        "custom_components.frakon_energy.providers.eon_tariff_parser",
        "custom_components.frakon_energy.tariff_parser_preview",
        "custom_components.frakon_energy.tariff_all_in_preview",
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
    pricing = _load(
        "custom_components.frakon_energy.pricing",
        "custom_components/frakon_energy/pricing.py",
    )
    sources = _load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    regulated_pricing = _load(
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components/frakon_energy/regulated_pricing.py",
    )
    selection = _load(
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components/frakon_energy/tariff_candidate_selection.py",
    )
    download = _load(
        "custom_components.frakon_energy.tariff_download",
        "custom_components/frakon_energy/tariff_download.py",
    )
    _load(
        "custom_components.frakon_energy.tariff_pdf_text",
        "custom_components/frakon_energy/tariff_pdf_text.py",
    )
    provenance = _load(
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components/frakon_energy/tariff_provenance.py",
    )
    _load(
        "custom_components.frakon_energy.tariff_assembly",
        "custom_components/frakon_energy/tariff_assembly.py",
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
    parser_preview = _load(
        "custom_components.frakon_energy.tariff_parser_preview",
        "custom_components/frakon_energy/tariff_parser_preview.py",
    )
    all_in = _load(
        "custom_components.frakon_energy.tariff_all_in_preview",
        "custom_components/frakon_energy/tariff_all_in_preview.py",
    )
    return (
        contracts,
        pricing,
        sources,
        regulated_pricing,
        selection,
        download,
        provenance,
        parser_preview,
        all_in,
    )


def test_eon_supplier_preview_combines_only_with_independent_confirmed_regulation() -> None:
    (
        contracts,
        pricing,
        sources,
        regulated_pricing,
        selection,
        download_module,
        provenance,
        parser_preview,
        all_in,
    ) = load_modules()

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
            source_url="https://www.eon.cz/getmedia/fixture-eon.pdf",
            discovered_at=datetime(2026, 8, 15, 7, 0, tzinfo=timezone.utc),
            document_date=date(2026, 3, 30),
            content_type="application/pdf",
        ),
        product_name="Variant PRO na 2 roky",
        valid_from=date(2026, 3, 30),
        valid_to=None,
        match_score=100,
        match_reasons=("exact verified fixture",),
        price_scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    )
    content = b"%PDF-1.7\nE.ON all-in fixture\n%%EOF"
    sha256 = hashlib.sha256(content).hexdigest()
    document = sources.OfficialTariffDocument(
        supplier="eon",
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
        validated_at=datetime(2026, 8, 15, 7, 1, tzinfo=timezone.utc),
    )
    parsed = parser_preview.SupplierTariffParsePreview(
        supplier="eon",
        product_name="Variant PRO na 2 roky",
        valid_from=date(2026, 3, 30),
        distribution_tariff="D25d",
        high_rate_czk_per_kwh=Decimal("3.320"),
        low_rate_czk_per_kwh=Decimal("2.987"),
        supplier_standing_czk_month=Decimal("168"),
        includes_vat=True,
        source_url=document.source_url,
        document_sha256=sha256,
        page_count=2,
        parser_name="eon_commercial_v1",
        extraction_method="pypdf_layout",
        extraction_confidence=100,
        validation_reasons=(
            "exact E.ON supplier-commercial fixture",
            "regulated supplier-PDF rows excluded",
        ),
    )

    regulated_checksum = "b" * 64
    regulated_url = "https://eru.gov.cz/energeticky-regulacni-vestnik-182025"
    regulated = regulated_pricing.RegulatedTariffBundle(
        distributor="eg_d",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        variable_components=(
            pricing.VariablePriceComponent(
                kind=pricing.PriceComponentKind.DISTRIBUTION,
                name="Regulovaná distribuce",
                high_rate_czk_per_kwh=Decimal("1.00"),
                low_rate_czk_per_kwh=Decimal("0.50"),
                includes_vat=False,
            ),
            pricing.VariablePriceComponent(
                kind=pricing.PriceComponentKind.SYSTEM_SERVICES,
                name="Systémové služby",
                high_rate_czk_per_kwh=Decimal("0.10"),
                low_rate_czk_per_kwh=Decimal("0.10"),
                includes_vat=False,
            ),
            pricing.VariablePriceComponent(
                kind=pricing.PriceComponentKind.POZE,
                name="POZE",
                high_rate_czk_per_kwh=Decimal("0"),
                low_rate_czk_per_kwh=Decimal("0"),
                includes_vat=False,
            ),
            pricing.VariablePriceComponent(
                kind=pricing.PriceComponentKind.ELECTRICITY_TAX,
                name="Daň z elektřiny",
                high_rate_czk_per_kwh=Decimal("0.0283"),
                low_rate_czk_per_kwh=Decimal("0.0283"),
                includes_vat=False,
            ),
        ),
        fixed_components=(
            pricing.FixedPriceComponent(
                kind=pricing.PriceComponentKind.BREAKER_FIXED,
                name="Plat za příkon podle hlavního jističe",
                monthly_czk=Decimal("200"),
                includes_vat=False,
            ),
            pricing.FixedPriceComponent(
                kind=pricing.PriceComponentKind.OTHER_FIXED,
                name=regulated_pricing.NON_NETWORK_INFRASTRUCTURE_COMPONENT_NAME,
                monthly_czk=Decimal("12.87"),
                includes_vat=False,
            ),
        ),
        source_url=regulated_url,
        document_date=date(2025, 11, 28),
        checksum=regulated_checksum,
        confirmed=True,
    )
    evidence = (
        provenance.PriceEvidence(
            scope=sources.PRICE_SCOPE_REGULATED,
            source_name="Energetický regulační úřad",
            document_name="Cenový výměr 14/2025 – E.ON fixture",
            source_url=regulated_url,
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
            document_date=date(2025, 11, 28),
            checksum=regulated_checksum,
        ),
    )

    result = all_in.build_all_in_tariff_preview(
        download=validated,
        parsed=parsed,
        contract=contract,
        regulated=regulated,
        regulated_evidence=evidence,
    )
    payload = result.as_dict()

    assert payload["supplier"] == "eon"
    assert payload["product_name"] == "Variant PRO na 2 roky"
    assert payload["all_in_vt_czk_kwh"] == "4.685243"
    assert payload["all_in_nt_czk_kwh"] == "3.747243"
    assert payload["fixed_monthly_total_czk"] == "425.5727"
    assert payload["variable_components"][0]["name"] == "E.ON – obchodní cena elektřiny"
    assert payload["fixed_components"][0]["name"] == "E.ON – stálá platba dodavatele"

    evidence_by_scope = {
        item["scope"]: item for item in payload["provenance"]["evidence"]
    }
    assert set(evidence_by_scope) == {
        sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
        sources.PRICE_SCOPE_REGULATED,
    }
    supplier_evidence = evidence_by_scope[sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL]
    regulated_evidence = evidence_by_scope[sources.PRICE_SCOPE_REGULATED]
    assert supplier_evidence["source_name"] == "E.ON Energie"
    assert supplier_evidence["source_url"] == document.source_url
    assert supplier_evidence["checksum"] == sha256
    assert regulated_evidence["source_url"] == regulated_url
    assert regulated_evidence["checksum"] == regulated_checksum
    assert result.persistence_performed is False
    assert result.activation_performed is False
