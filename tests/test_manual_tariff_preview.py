from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys

import pytest


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _fixture(*, confirmed=True):
    helpers = _load(
        "_frakon_test_tariff_all_in_preview_helpers",
        "tests/test_tariff_all_in_preview.py",
    )
    (
        sources,
        provenance,
        _automatic_all_in,
        contract,
        validated,
        parsed,
        regulated,
        evidence,
    ) = helpers._fixture(confirmed=confirmed)
    _load(
        "custom_components.frakon_energy.all_in_catalog",
        "custom_components/frakon_energy/all_in_catalog.py",
    )
    authority = _load(
        "custom_components.frakon_energy.all_in_authority",
        "custom_components/frakon_energy/all_in_authority.py",
    )
    manual = _load(
        "custom_components.frakon_energy.manual_tariff_preview",
        "custom_components/frakon_energy/manual_tariff_preview.py",
    )
    return (
        sources,
        provenance,
        authority,
        manual,
        contract,
        validated,
        parsed,
        regulated,
        evidence,
    )


def _manual_input(manual, parsed):
    return manual.ManualSupplierCommercialInput(
        high_rate_czk_per_kwh=parsed.high_rate_czk_per_kwh,
        low_rate_czk_per_kwh=parsed.low_rate_czk_per_kwh,
        supplier_standing_czk_month=parsed.supplier_standing_czk_month,
    )


def test_manual_preview_combines_only_manual_commercial_with_confirmed_regulated_prices() -> None:
    (
        _sources,
        _provenance,
        authority,
        manual,
        contract,
        validated,
        parsed,
        regulated,
        evidence,
    ) = _fixture()

    result = manual.build_manual_all_in_tariff_preview(
        download=validated,
        manual_commercial=_manual_input(manual, parsed),
        contract=contract,
        regulated=regulated,
        regulated_evidence=evidence,
    )

    assert str(result.assembly.all_in_vt_czk_kwh) == "5.325243"
    assert str(result.assembly.all_in_nt_czk_kwh) == "4.460243"
    assert str(result.assembly.fixed_monthly_total_czk) == "388.2527"
    payload = result.as_dict()
    assert payload["authority_method"] == authority.AllInTariffAuthorityMethod.MANUAL_USER_ENTRY.value
    assert payload["manual_entry"] is True
    assert payload["parsing_performed"] is False
    assert payload["persistence_performed"] is False
    assert payload["activation_performed"] is False
    assert payload["manual_supplier_commercial"] == {
        "high_rate_czk_per_kwh": "3.96",
        "low_rate_czk_per_kwh": "3.70",
        "supplier_standing_czk_month": "130.68",
        "includes_vat": True,
    }
    assert len(payload["provenance"]["evidence"]) == 2
    assert payload["supplier_document_sha256"] == validated.document.sha256


def test_manual_input_is_explicit_gross_decimal_and_rejects_invalid_values() -> None:
    *_prefix, manual, _contract, _validated, _parsed, _regulated, _evidence = _fixture()

    valid = manual.ManualSupplierCommercialInput(
        high_rate_czk_per_kwh=Decimal("0"),
        low_rate_czk_per_kwh=Decimal("0.001"),
        supplier_standing_czk_month=Decimal("0"),
    )
    assert valid.includes_vat is True

    with pytest.raises(ValueError, match="must be Decimal"):
        manual.ManualSupplierCommercialInput(
            high_rate_czk_per_kwh="3.50",
            low_rate_czk_per_kwh=Decimal("3.40"),
            supplier_standing_czk_month=Decimal("120"),
        )
    with pytest.raises(ValueError, match="finite and non-negative"):
        manual.ManualSupplierCommercialInput(
            high_rate_czk_per_kwh=Decimal("-1"),
            low_rate_czk_per_kwh=Decimal("3.40"),
            supplier_standing_czk_month=Decimal("120"),
        )
    with pytest.raises(ValueError, match="finite and non-negative"):
        manual.ManualSupplierCommercialInput(
            high_rate_czk_per_kwh=Decimal("NaN"),
            low_rate_czk_per_kwh=Decimal("3.40"),
            supplier_standing_czk_month=Decimal("120"),
        )


def test_manual_preview_requires_exact_candidate_fingerprint_product_and_pinned_sha() -> None:
    (
        sources,
        _provenance,
        _authority,
        manual,
        contract,
        validated,
        parsed,
        regulated,
        evidence,
    ) = _fixture()
    download_module = sys.modules["custom_components.frakon_energy.tariff_download"]
    selection = sys.modules["custom_components.frakon_energy.tariff_candidate_selection"]
    manual_input = _manual_input(manual, parsed)

    wrong_selection = download_module.ValidatedTariffDownload(
        selected_fingerprint="0" * 64,
        candidate=validated.candidate,
        document=validated.document,
        content=validated.content,
        validated_at=validated.validated_at,
    )
    with pytest.raises(ValueError, match="selected fingerprint"):
        manual.build_manual_all_in_tariff_preview(
            download=wrong_selection,
            manual_commercial=manual_input,
            contract=contract,
            regulated=regulated,
            regulated_evidence=evidence,
        )

    wrong_product = replace(contract, product_name="Different product")
    with pytest.raises(ValueError, match="product does not match"):
        manual.build_manual_all_in_tariff_preview(
            download=validated,
            manual_commercial=manual_input,
            contract=wrong_product,
            regulated=regulated,
            regulated_evidence=evidence,
        )

    pinned_candidate = sources.TariffDocumentCandidate(
        document=replace(validated.candidate.document, sha256="f" * 64),
        product_name=validated.candidate.product_name,
        valid_from=validated.candidate.valid_from,
        valid_to=validated.candidate.valid_to,
        match_score=validated.candidate.match_score,
        match_reasons=validated.candidate.match_reasons,
        price_scope=validated.candidate.price_scope,
    )
    pinned_download = download_module.ValidatedTariffDownload(
        selected_fingerprint=selection.tariff_candidate_selection_fingerprint(pinned_candidate),
        candidate=pinned_candidate,
        document=validated.document,
        content=validated.content,
        validated_at=validated.validated_at,
    )
    with pytest.raises(ValueError, match="pinned candidate"):
        manual.build_manual_all_in_tariff_preview(
            download=pinned_download,
            manual_commercial=manual_input,
            contract=contract,
            regulated=regulated,
            regulated_evidence=evidence,
        )


def test_manual_preview_still_requires_confirmed_regulator() -> None:
    (
        _sources,
        _provenance,
        _authority,
        manual,
        contract,
        validated,
        parsed,
        regulated,
        evidence,
    ) = _fixture(confirmed=False)

    with pytest.raises(ValueError, match="must be confirmed"):
        manual.build_manual_all_in_tariff_preview(
            download=validated,
            manual_commercial=_manual_input(manual, parsed),
            contract=contract,
            regulated=regulated,
            regulated_evidence=evidence,
        )


def test_mnd_sha_pinned_candidate_can_use_manual_preview_without_parser_authority() -> None:
    (
        sources,
        _provenance,
        authority,
        manual,
        _cez_contract,
        validated,
        _parsed,
        regulated,
        evidence,
    ) = _fixture()
    contracts = sys.modules["custom_components.frakon_energy.contracts"]
    download_module = sys.modules["custom_components.frakon_energy.tariff_download"]
    selection = sys.modules["custom_components.frakon_energy.tariff_candidate_selection"]

    contract = contracts.ElectricityContract(
        supplier=contracts.Supplier.MND,
        distributor=contracts.Distributor.CEZ_DISTRIBUCE,
        product_name="Proud - Ceník Říjen 28",
        contract_kind=contracts.ContractKind.FIXED,
        distribution_tariff="D25d",
        breaker=contracts.Breaker(phases=3, amperes=25),
        valid_from=date(2026, 6, 11),
        valid_to=None,
        fixation_end=date(2028, 10, 31),
        customer_confirmed=False,
    )
    source_url = (
        "https://prod.mnd.cz/documents/view/"
        "12345678-1234-4234-8234-123456789abc"
    )
    candidate_document = sources.OfficialTariffDocument(
        supplier="mnd",
        source_url=source_url,
        discovered_at=datetime(2026, 8, 15, 17, 0, tzinfo=timezone.utc),
        document_date=date(2026, 6, 11),
        sha256=validated.document.sha256,
        content_type="application/pdf",
    )
    candidate = sources.TariffDocumentCandidate(
        document=candidate_document,
        product_name=contract.product_name,
        valid_from=date(2026, 6, 11),
        valid_to=date(2028, 10, 31),
        match_score=100,
        match_reasons=("confirmed MND source resolver fixture",),
        price_scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    )
    document = replace(
        candidate_document,
        discovered_at=validated.document.discovered_at,
    )
    download = download_module.ValidatedTariffDownload(
        selected_fingerprint=selection.tariff_candidate_selection_fingerprint(candidate),
        candidate=candidate,
        document=document,
        content=validated.content,
        validated_at=validated.validated_at,
    )
    manual_input = manual.ManualSupplierCommercialInput(
        high_rate_czk_per_kwh=Decimal("2.899"),
        low_rate_czk_per_kwh=Decimal("2.899"),
        supplier_standing_czk_month=Decimal("168"),
    )

    result = manual.build_manual_all_in_tariff_preview(
        download=download,
        manual_commercial=manual_input,
        contract=contract,
        regulated=regulated,
        regulated_evidence=evidence,
    )

    assert result.assembly.supplier == "mnd"
    assert result.assembly.product_name == contract.product_name
    assert result.authority_method is authority.AllInTariffAuthorityMethod.MANUAL_USER_ENTRY
    assert result.parsing_performed is False
    supplier_evidence = result.assembly.provenance.evidence_for_scope(
        sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL
    )
    assert len(supplier_evidence) == 1
    assert supplier_evidence[0].source_name == "MND Energie"
    assert supplier_evidence[0].source_url == source_url
    assert supplier_evidence[0].checksum == validated.document.sha256


def test_manual_preview_rejects_non_exact_candidate_score() -> None:
    (
        sources,
        _provenance,
        _authority,
        manual,
        contract,
        validated,
        parsed,
        regulated,
        evidence,
    ) = _fixture()
    selection = sys.modules["custom_components.frakon_energy.tariff_candidate_selection"]
    download_module = sys.modules["custom_components.frakon_energy.tariff_download"]
    candidate = sources.TariffDocumentCandidate(
        document=validated.candidate.document,
        product_name=validated.candidate.product_name,
        valid_from=validated.candidate.valid_from,
        valid_to=validated.candidate.valid_to,
        match_score=99,
        match_reasons=("not exact",),
        price_scope=validated.candidate.price_scope,
    )
    download = download_module.ValidatedTariffDownload(
        selected_fingerprint=selection.tariff_candidate_selection_fingerprint(candidate),
        candidate=candidate,
        document=validated.document,
        content=validated.content,
        validated_at=validated.validated_at,
    )

    with pytest.raises(ValueError, match="100-score"):
        manual.build_manual_all_in_tariff_preview(
            download=download,
            manual_commercial=_manual_input(manual, parsed),
            contract=contract,
            regulated=regulated,
            regulated_evidence=evidence,
        )


def test_manual_preview_revalidates_official_supplier_domain() -> None:
    (
        sources,
        _provenance,
        _authority,
        manual,
        contract,
        validated,
        parsed,
        regulated,
        evidence,
    ) = _fixture()
    selection = sys.modules["custom_components.frakon_energy.tariff_candidate_selection"]
    download_module = sys.modules["custom_components.frakon_energy.tariff_download"]
    foreign_url = "https://example.invalid/pricelist.pdf"
    candidate_document = replace(validated.candidate.document, source_url=foreign_url)
    candidate = sources.TariffDocumentCandidate(
        document=candidate_document,
        product_name=validated.candidate.product_name,
        valid_from=validated.candidate.valid_from,
        valid_to=validated.candidate.valid_to,
        match_score=100,
        match_reasons=("synthetic drift fixture",),
        price_scope=validated.candidate.price_scope,
    )
    document = replace(validated.document, source_url=foreign_url)
    download = download_module.ValidatedTariffDownload(
        selected_fingerprint=selection.tariff_candidate_selection_fingerprint(candidate),
        candidate=candidate,
        document=document,
        content=validated.content,
        validated_at=validated.validated_at,
    )

    with pytest.raises(ValueError, match="official domain"):
        manual.build_manual_all_in_tariff_preview(
            download=download,
            manual_commercial=_manual_input(manual, parsed),
            contract=contract,
            regulated=regulated,
            regulated_evidence=evidence,
        )


def test_manual_preview_rejects_regulated_bundle_for_other_distributor() -> None:
    (
        _sources,
        _provenance,
        _authority,
        manual,
        contract,
        validated,
        parsed,
        regulated,
        evidence,
    ) = _fixture()

    with pytest.raises(ValueError, match="regulated distributor"):
        manual.build_manual_all_in_tariff_preview(
            download=validated,
            manual_commercial=_manual_input(manual, parsed),
            contract=contract,
            regulated=replace(regulated, distributor="eg_d"),
            regulated_evidence=evidence,
        )
