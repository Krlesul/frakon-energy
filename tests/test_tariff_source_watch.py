from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types


def load_modules():
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.pricing",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components.frakon_energy.cz_regulated_sources",
        "custom_components.frakon_energy.tariff_assembly",
        "custom_components.frakon_energy.all_in_catalog",
        "custom_components.frakon_energy.tariff_source_watch",
    )
    for name in names:
        sys.modules.pop(name, None)

    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, Path(path))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    pricing = load(
        "custom_components.frakon_energy.pricing",
        "custom_components/frakon_energy/pricing.py",
    )
    sources = load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    provenance = load(
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components/frakon_energy/tariff_provenance.py",
    )
    load(
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components/frakon_energy/regulated_pricing.py",
    )
    cz = load(
        "custom_components.frakon_energy.cz_regulated_sources",
        "custom_components/frakon_energy/cz_regulated_sources.py",
    )
    assembly = load(
        "custom_components.frakon_energy.tariff_assembly",
        "custom_components/frakon_energy/tariff_assembly.py",
    )
    catalog = load(
        "custom_components.frakon_energy.all_in_catalog",
        "custom_components/frakon_energy/all_in_catalog.py",
    )
    watch = load(
        "custom_components.frakon_energy.tariff_source_watch",
        "custom_components/frakon_energy/tariff_source_watch.py",
    )
    return pricing, sources, provenance, cz, assembly, catalog, watch


def _confirmed_item(modules, *, confirmed=True):
    pricing, sources, provenance, cz, assembly, catalog, _ = modules
    eru = cz.RegulatedPriceSource(
        authority=cz.RegulatedAuthority.ERU,
        document_id="Cenový výměr 14/2025",
        source_url="https://eru.gov.cz/energeticky-regulacni-vestnik-182025",
        valid_from=date(2026, 1, 1),
    )
    ote = cz.RegulatedPriceSource(
        authority=cz.RegulatedAuthority.OTE,
        document_id="OTE 2026",
        source_url="https://www.ote-cr.cz/cs/registrace-a-smlouvy/smluvni-vztahy-elektrina/ceny-za-sluzby-ote",
        valid_from=date(2026, 1, 1),
    )
    inputs = cz.CzechRegulatedTariffInputs(
        distributor="cez_distribuce",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        distribution_vt_czk_per_kwh=Decimal("1.000"),
        distribution_nt_czk_per_kwh=Decimal("0.500"),
        breaker_monthly_czk=Decimal("200"),
        system_services_czk_per_kwh=Decimal("0.100"),
        electricity_tax_czk_per_kwh=Decimal("0.02830"),
        sources=(eru, ote),
    )
    regulated = inputs.to_bundle(confirmed=True)
    supplier_evidence = provenance.PriceEvidence(
        scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
        source_name="ČEZ Prodej",
        document_name="Basic 2026",
        source_url="https://www.cez.cz/file/edee/basic-2026.pdf",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        document_date=date(2025, 10, 1),
        checksum="a" * 64,
    )
    evidence = provenance.MultiSourceTariffProvenance(
        (supplier_evidence, *inputs.regulated_evidence())
    )
    commodity = pricing.VariablePriceComponent(
        pricing.PriceComponentKind.COMMODITY,
        "ČEZ Basic commodity",
        Decimal("3.960"),
        Decimal("3.700"),
    )
    supplier_fixed = pricing.FixedPriceComponent(
        pricing.PriceComponentKind.SUPPLIER_FIXED,
        "ČEZ Basic stálá platba",
        Decimal("130.68"),
    )
    all_in = assembly.assemble_all_in_tariff(
        supplier="ČEZ",
        product_name="Basic",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        commercial_valid_from=date(2026, 1, 1),
        commercial_valid_to=date(2026, 12, 31),
        commodity=commodity,
        supplier_fixed=supplier_fixed,
        regulated=regulated,
        provenance=evidence,
    )
    return catalog.PersistedAllInTariff(assembly=all_in, confirmed=confirmed)


def _checked_at():
    return datetime(2026, 8, 14, 7, 30, tzinfo=timezone.utc)


def test_watch_can_only_be_created_from_confirmed_all_in_supplier_evidence() -> None:
    modules = load_modules()
    *_, watch = modules

    try:
        watch.source_watch_from_confirmed_all_in(
            _confirmed_item(modules, confirmed=False),
            supplier="cez",
        )
    except ValueError as err:
        assert "confirmed" in str(err)
    else:
        raise AssertionError("Unconfirmed all-in tariff must not create a source watch")

    result = watch.source_watch_from_confirmed_all_in(
        _confirmed_item(modules),
        supplier="cez",
        etag='"active-v1"',
        last_modified="Thu, 01 Jan 2026 00:00:00 GMT",
    )
    assert result.supplier == "cez"
    assert result.product_name == "Basic"
    assert result.source_url == "https://www.cez.cz/file/edee/basic-2026.pdf"
    assert result.active_sha256 == "a" * 64
    assert result.document_date == date(2025, 10, 1)
    assert result.etag == '"active-v1"'


def test_watch_round_trip_and_identity_ignore_mutable_hash_and_http_validators() -> None:
    modules = load_modules()
    *_, watch = modules
    original = watch.source_watch_from_confirmed_all_in(
        _confirmed_item(modules),
        supplier="cez",
        etag='"v1"',
    )
    restored = watch.TariffSourceWatch.from_dict(original.as_dict())
    assert restored == original

    changed_operational_state = replace(
        original,
        active_sha256="b" * 64,
        etag='"v2"',
        last_modified="Fri, 14 Aug 2026 05:00:00 GMT",
    )
    assert watch.tariff_source_watch_fingerprint(original) == (
        watch.tariff_source_watch_fingerprint(changed_operational_state)
    )

    payload = original.as_dict()
    payload["schema_version"] = 999
    try:
        watch.TariffSourceWatch.from_dict(payload)
    except ValueError as err:
        assert "unsupported tariff source watch schema" in str(err)
    else:
        raise AssertionError("Unknown source-watch schema must fail closed")


def test_not_modified_never_changes_active_authority() -> None:
    modules = load_modules()
    *_, watch = modules
    source_watch = watch.source_watch_from_confirmed_all_in(
        _confirmed_item(modules),
        supplier="cez",
        etag='"v1"',
    )

    result = watch.tariff_source_not_modified(
        source_watch,
        checked_at=_checked_at(),
        etag='"v1"',
    )

    assert result.status == watch.STATUS_NOT_MODIFIED
    assert result.active_sha256 == "a" * 64
    assert result.observed_sha256 is None
    assert result.active_unchanged is True
    assert result.requires_confirmation is False
    assert result.persistence_performed is False
    assert result.activation_performed is False


def test_same_hash_is_unchanged_and_new_hash_is_only_a_confirmation_required_proposal() -> None:
    modules = load_modules()
    _, sources, *_, watch = modules
    source_watch = watch.source_watch_from_confirmed_all_in(
        _confirmed_item(modules),
        supplier="cez",
    )

    unchanged_document = sources.OfficialTariffDocument(
        supplier="cez",
        source_url=source_watch.source_url,
        discovered_at=_checked_at(),
        sha256="a" * 64,
        etag='"same"',
        content_type="application/pdf",
    )
    unchanged = watch.evaluate_tariff_source_download(
        source_watch,
        document=unchanged_document,
        checked_at=_checked_at(),
    )
    assert unchanged.status == watch.STATUS_UNCHANGED_HASH
    assert unchanged.observed_sha256 == unchanged.active_sha256 == "a" * 64
    assert unchanged.requires_confirmation is False

    changed_document = sources.OfficialTariffDocument(
        supplier="cez",
        source_url=source_watch.source_url,
        discovered_at=_checked_at(),
        sha256="b" * 64,
        etag='"new"',
        content_type="application/pdf",
    )
    changed = watch.evaluate_tariff_source_download(
        source_watch,
        document=changed_document,
        checked_at=_checked_at(),
    )
    assert changed.status == watch.STATUS_CHANGE_DETECTED
    assert changed.active_sha256 == "a" * 64
    assert changed.observed_sha256 == "b" * 64
    assert changed.active_unchanged is True
    assert changed.requires_confirmation is True
    assert changed.persistence_performed is False
    assert changed.activation_performed is False


def test_watch_rejects_observation_from_different_supplier_or_url() -> None:
    modules = load_modules()
    _, sources, *_, watch = modules
    source_watch = watch.source_watch_from_confirmed_all_in(
        _confirmed_item(modules),
        supplier="cez",
    )

    documents = (
        sources.OfficialTariffDocument(
            supplier="eon",
            source_url=source_watch.source_url,
            discovered_at=_checked_at(),
            sha256="b" * 64,
        ),
        sources.OfficialTariffDocument(
            supplier="cez",
            source_url="https://www.cez.cz/file/edee/other.pdf",
            discovered_at=_checked_at(),
            sha256="b" * 64,
        ),
    )
    for document in documents:
        try:
            watch.evaluate_tariff_source_download(
                source_watch,
                document=document,
                checked_at=_checked_at(),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Source-watch observation identity drift must fail closed")


def test_source_check_error_preserves_active_hash_and_carries_no_change_authority() -> None:
    modules = load_modules()
    *_, watch = modules
    source_watch = watch.source_watch_from_confirmed_all_in(
        _confirmed_item(modules),
        supplier="cez",
    )

    result = watch.tariff_source_check_error(
        source_watch,
        checked_at=_checked_at(),
        error="network timeout",
    )

    assert result.status == watch.STATUS_ERROR
    assert result.error == "network timeout"
    assert result.active_sha256 == "a" * 64
    assert result.observed_sha256 is None
    assert result.active_unchanged is True
    assert result.requires_confirmation is False
