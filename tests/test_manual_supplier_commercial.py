from dataclasses import replace
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
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.pricing",
        "custom_components.frakon_energy.contracts",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components.frakon_energy.tariff_download",
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components.frakon_energy.tariff_assembly",
        "custom_components.frakon_energy.all_in_catalog",
        "custom_components.frakon_energy.all_in_authority",
        "custom_components.frakon_energy.manual_supplier_commercial",
    )
    for name in names:
        sys.modules.pop(name, None)
    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    pricing = _load(
        "custom_components.frakon_energy.pricing",
        "custom_components/frakon_energy/pricing.py",
    )
    contracts = _load(
        "custom_components.frakon_energy.contracts",
        "custom_components/frakon_energy/contracts.py",
    )
    sources = _load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    regulated = _load(
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
    provenance = _load(
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components/frakon_energy/tariff_provenance.py",
    )
    _load(
        "custom_components.frakon_energy.tariff_assembly",
        "custom_components/frakon_energy/tariff_assembly.py",
    )
    _load(
        "custom_components.frakon_energy.all_in_catalog",
        "custom_components/frakon_energy/all_in_catalog.py",
    )
    authority = _load(
        "custom_components.frakon_energy.all_in_authority",
        "custom_components/frakon_energy/all_in_authority.py",
    )
    manual = _load(
        "custom_components.frakon_energy.manual_supplier_commercial",
        "custom_components/frakon_energy/manual_supplier_commercial.py",
    )
    return pricing, contracts, sources, regulated, selection, download, provenance, authority, manual


def _fixture(*, candidate_score: int = 100, candidate_supplier: str = "mnd"):
    pricing, contracts, sources, regulated_module, selection, download_module, provenance, authority, manual = load_modules()
    contract = contracts.ElectricityContract(
        supplier=contracts.Supplier.MND,
        distributor=contracts.Distributor.CEZ_DISTRIBUCE,
        product_name="Proud - Ceník Říjen 28",
        contract_kind=contracts.ContractKind.FIXED,
        distribution_tariff="D25d",
        breaker=contracts.Breaker(phases=3, amperes=25),
        valid_from=date(2026, 6, 11),
        valid_to=date(2028, 10, 31),
        fixation_end=date(2028, 10, 31),
        customer_confirmed=False,
    )
    source_url = (
        "https://prod.mnd.cz/documents/view/"
        "12345678-1234-4234-8234-123456789abc"
    )
    content = b"%PDF-1.7\nmanual MND supplier-commercial fixture\n%%EOF"
    sha256 = hashlib.sha256(content).hexdigest()
    discovered_at = datetime(2026, 8, 15, 17, 0, tzinfo=timezone.utc)
    candidate = sources.TariffDocumentCandidate(
        document=sources.OfficialTariffDocument(
            supplier=candidate_supplier,
            source_url=source_url,
            discovered_at=discovered_at,
            document_date=date(2026, 6, 11),
            sha256=sha256,
            content_type="application/pdf",
        ),
        product_name="Proud - Ceník Říjen 28",
        valid_from=date(2026, 6, 11),
        valid_to=date(2028, 10, 31),
        match_score=candidate_score,
        match_reasons=("exact confirmed MND source fixture",),
        price_scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    )
    validated = download_module.ValidatedTariffDownload(
        selected_fingerprint=selection.tariff_candidate_selection_fingerprint(candidate),
        candidate=candidate,
        document=sources.OfficialTariffDocument(
            supplier=candidate_supplier,
            source_url=source_url,
            discovered_at=discovered_at,
            document_date=date(2026, 6, 11),
            sha256=sha256,
            content_type="application/pdf",
        ),
        content=content,
        validated_at=datetime(2026, 8, 15, 17, 1, tzinfo=timezone.utc),
    )
    regulated_url = "https://eru.gov.cz/energeticky-regulacni-vestnik-182025"
    regulated_checksum = "b" * 64
    regulated = regulated_module.RegulatedTariffBundle(
        distributor="cez_distribuce",
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
                name=regulated_module.NON_NETWORK_INFRASTRUCTURE_COMPONENT_NAME,
                monthly_czk=Decimal("12.87"),
                includes_vat=False,
            ),
        ),
        source_url=regulated_url,
        document_date=date(2025, 11, 28),
        checksum=regulated_checksum,
        confirmed=True,
    )
    regulated_evidence = (
        provenance.PriceEvidence(
            scope=sources.PRICE_SCOPE_REGULATED,
            source_name="Energetický regulační úřad",
            document_name="Cenový výměr 14/2025 – fixture",
            source_url=regulated_url,
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
            document_date=date(2025, 11, 28),
            checksum=regulated_checksum,
        ),
    )
    values = manual.ManualSupplierCommercialValues.from_dict(
        {
            "high_rate_czk_per_kwh": "2.899",
            "low_rate_czk_per_kwh": "2.899",
            "supplier_standing_czk_month": "168",
        }
    )
    return (
        pricing,
        contracts,
        sources,
        regulated_module,
        selection,
        download_module,
        provenance,
        authority,
        manual,
        contract,
        validated,
        regulated,
        regulated_evidence,
        values,
    )


def test_manual_mnd_preview_builds_all_in_with_explicit_manual_authority() -> None:
    *_, authority, manual, contract, validated, regulated, evidence, values = _fixture()

    preview = manual.build_manual_supplier_commercial_preview(
        download=validated,
        contract=contract,
        regulated=regulated,
        regulated_evidence=evidence,
        values=values,
    )

    assert preview.authority_method is authority.AllInTariffAuthorityMethod.MANUAL_USER_ENTRY
    assert preview.parsing_performed is False
    assert preview.persistence_performed is False
    assert preview.activation_performed is False
    assert str(preview.assembly.all_in_vt_czk_kwh) == "4.264243"
    assert str(preview.assembly.all_in_nt_czk_kwh) == "3.659243"
    assert str(preview.assembly.fixed_monthly_total_czk) == "425.5727"
    assert preview.assembly.valid_from == date(2026, 6, 11)
    assert preview.assembly.valid_to == date(2026, 12, 31)

    payload = preview.as_dict()
    assert payload["authority_method"] == "manual_user_entry"
    assert payload["manual_entry_performed"] is True
    assert payload["parsing_performed"] is False
    assert payload["all_in_ready"] is True
    assert payload["supplier_source_url"].startswith("https://prod.mnd.cz/documents/view/")
    assert payload["supplier_document_sha256"] == validated.document.sha256
    supplier_evidence = [
        item
        for item in payload["provenance"]["evidence"]
        if item["scope"] == "supplier_commercial"
    ]
    assert len(supplier_evidence) == 1
    assert supplier_evidence[0]["checksum"] == validated.document.sha256


def test_manual_value_payload_accepts_only_three_exact_decimal_strings() -> None:
    *_, manual, _contract, _validated, _regulated, _evidence, _values = _fixture()
    valid = {
        "high_rate_czk_per_kwh": "2.899",
        "low_rate_czk_per_kwh": "2.7990",
        "supplier_standing_czk_month": "168.00",
    }
    parsed = manual.ManualSupplierCommercialValues.from_dict(valid)
    assert parsed.as_dict() == valid

    forbidden_payloads = (
        {**valid, "all_in_vt_czk_kwh": "5.00"},
        {**valid, "distribution_vt_czk_per_kwh": "1.00"},
        {**valid, "regulated_price": "1.00"},
        {**valid, "source_url": "https://example.invalid/tariff.pdf"},
        {**valid, "includes_vat": True},
    )
    for payload in forbidden_payloads:
        with pytest.raises(ValueError, match="unsupported fields"):
            manual.ManualSupplierCommercialValues.from_dict(payload)

    with pytest.raises(ValueError, match="missing fields"):
        manual.ManualSupplierCommercialValues.from_dict(
            {
                "high_rate_czk_per_kwh": "2.899",
                "supplier_standing_czk_month": "168",
            }
        )
    with pytest.raises(ValueError, match="decimal string"):
        manual.ManualSupplierCommercialValues.from_dict(
            {**valid, "high_rate_czk_per_kwh": 2.899}
        )
    with pytest.raises(ValueError, match="finite non-negative"):
        manual.ManualSupplierCommercialValues.from_dict(
            {**valid, "high_rate_czk_per_kwh": "-1"}
        )
    with pytest.raises(ValueError, match="finite non-negative"):
        manual.ManualSupplierCommercialValues.from_dict(
            {**valid, "high_rate_czk_per_kwh": "NaN"}
        )


def test_manual_preview_revalidates_exact_candidate_selection_and_match_quality() -> None:
    *_, manual, contract, validated, regulated, evidence, values = _fixture()
    drifted = replace(validated, selected_fingerprint="0" * 64)
    with pytest.raises(ValueError, match="selected fingerprint"):
        manual.build_manual_supplier_commercial_preview(
            download=drifted,
            contract=contract,
            regulated=regulated,
            regulated_evidence=evidence,
            values=values,
        )

    *_, manual, contract, low_score, regulated, evidence, values = _fixture(
        candidate_score=99
    )
    with pytest.raises(ValueError, match="100-score"):
        manual.build_manual_supplier_commercial_preview(
            download=low_score,
            contract=contract,
            regulated=regulated,
            regulated_evidence=evidence,
            values=values,
        )


def test_manual_preview_rejects_supplier_product_and_official_domain_drift() -> None:
    *_, manual, contract, validated, regulated, evidence, values = _fixture()
    wrong_product = replace(contract, product_name="Proud - Domácnosti")
    with pytest.raises(ValueError, match="product"):
        manual.build_manual_supplier_commercial_preview(
            download=validated,
            contract=wrong_product,
            regulated=regulated,
            regulated_evidence=evidence,
            values=values,
        )

    sources = sys.modules["custom_components.frakon_energy.tariff_sources"]
    evil_candidate = replace(
        validated.candidate,
        document=replace(
            validated.candidate.document,
            source_url=(
                "https://example.invalid/documents/view/"
                "12345678-1234-4234-8234-123456789abc"
            ),
        ),
    )
    evil_download = replace(
        validated,
        candidate=evil_candidate,
        selected_fingerprint=sys.modules[
            "custom_components.frakon_energy.tariff_candidate_selection"
        ].tariff_candidate_selection_fingerprint(evil_candidate),
        document=sources.OfficialTariffDocument(
            supplier="mnd",
            source_url=evil_candidate.document.source_url,
            discovered_at=validated.document.discovered_at,
            document_date=validated.document.document_date,
            sha256=validated.document.sha256,
            content_type="application/pdf",
        ),
    )
    with pytest.raises(ValueError, match="official domain"):
        manual.build_manual_supplier_commercial_preview(
            download=evil_download,
            contract=contract,
            regulated=regulated,
            regulated_evidence=evidence,
            values=values,
        )


def test_manual_preview_requires_confirmed_exact_regulator_and_evidence() -> None:
    *_, manual, contract, validated, regulated, evidence, values = _fixture()
    with pytest.raises(ValueError, match="must be confirmed"):
        manual.build_manual_supplier_commercial_preview(
            download=validated,
            contract=contract,
            regulated=replace(regulated, confirmed=False),
            regulated_evidence=evidence,
            values=values,
        )

    with pytest.raises(ValueError, match="distributor"):
        manual.build_manual_supplier_commercial_preview(
            download=validated,
            contract=contract,
            regulated=replace(regulated, distributor="eg_d"),
            regulated_evidence=evidence,
            values=values,
        )

    provenance = sys.modules["custom_components.frakon_energy.tariff_provenance"]
    sources = sys.modules["custom_components.frakon_energy.tariff_sources"]
    wrong_evidence = (
        provenance.PriceEvidence(
            scope=sources.PRICE_SCOPE_REGULATED,
            source_name="ERÚ",
            document_name="wrong checksum",
            source_url=regulated.source_url,
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
            checksum="c" * 64,
        ),
    )
    with pytest.raises(ValueError, match="checksum"):
        manual.build_manual_supplier_commercial_preview(
            download=validated,
            contract=contract,
            regulated=regulated,
            regulated_evidence=wrong_evidence,
            values=values,
        )
