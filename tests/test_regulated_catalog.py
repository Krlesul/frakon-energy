from copy import deepcopy
from datetime import date
from decimal import Decimal
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
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components.frakon_energy.regulated_catalog",
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
    sources = _load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    regulated = _load(
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components/frakon_energy/regulated_pricing.py",
    )
    provenance = _load(
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components/frakon_energy/tariff_provenance.py",
    )
    catalog = _load(
        "custom_components.frakon_energy.regulated_catalog",
        "custom_components/frakon_energy/regulated_catalog.py",
    )
    return pricing, sources, regulated, provenance, catalog


def _version_from_modules(
    modules,
    *,
    valid_from=date(2026, 1, 1),
    valid_to=date(2026, 12, 31),
    checksum="a" * 64,
    confirmed=True,
):
    pricing, sources, regulated, provenance, catalog = modules
    source_url = "https://eru.gov.cz/energeticky-regulacni-vestnik-182025"
    bundle = regulated.RegulatedTariffBundle(
        distributor="cez_distribuce",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_from=valid_from,
        valid_to=valid_to,
        variable_components=(
            pricing.VariablePriceComponent(
                kind=pricing.PriceComponentKind.DISTRIBUTION,
                name="Distribuce",
                high_rate_czk_per_kwh=Decimal("1.1234"),
                low_rate_czk_per_kwh=Decimal("0.5678"),
                includes_vat=False,
            ),
            pricing.VariablePriceComponent(
                kind=pricing.PriceComponentKind.SYSTEM_SERVICES,
                name="Systémové služby",
                high_rate_czk_per_kwh=Decimal("0.1000"),
                low_rate_czk_per_kwh=Decimal("0.1000"),
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
                name="Jistič",
                monthly_czk=Decimal("200.00"),
                includes_vat=False,
            ),
            pricing.FixedPriceComponent(
                kind=pricing.PriceComponentKind.OTHER_FIXED,
                name=regulated.NON_NETWORK_INFRASTRUCTURE_COMPONENT_NAME,
                monthly_czk=Decimal("12.87"),
                includes_vat=False,
            ),
        ),
        source_url=source_url,
        document_date=date(2025, 11, 28),
        checksum=checksum,
        confirmed=confirmed,
    )
    evidence = (
        provenance.PriceEvidence(
            scope=sources.PRICE_SCOPE_REGULATED,
            source_name="Energetický regulační úřad",
            document_name="Cenový výměr 14/2025",
            source_url=source_url,
            valid_from=valid_from,
            valid_to=valid_to,
            document_date=date(2025, 11, 28),
            checksum=checksum,
        ),
    )
    return catalog.ConfirmedRegulatedTariffVersion(bundle=bundle, evidence=evidence)


def _version(**kwargs):
    modules = load_modules()
    return modules[-1], _version_from_modules(modules, **kwargs)


def test_confirmed_regulated_version_round_trips_exact_decimal_and_evidence_state() -> None:
    catalog, version = _version()

    encoded = version.as_dict()
    restored = catalog.ConfirmedRegulatedTariffVersion.from_dict(encoded)

    assert restored == version
    assert restored.fingerprint == version.fingerprint
    assert encoded["bundle"]["variable_components"][0]["high_rate_czk_per_kwh"] == "1.1234"
    assert encoded["bundle"]["fixed_components"][0]["monthly_czk"] == "200.00"
    assert encoded["bundle"]["confirmed"] is True
    assert encoded["evidence"][0]["checksum"] == "a" * 64


def test_catalog_rejects_unconfirmed_bundle_instead_of_creating_confirmation_authority() -> None:
    pricing, sources, regulated, provenance, catalog = load_modules()
    source_url = "https://eru.gov.cz/energeticky-regulacni-vestnik-182025"
    bundle = regulated.RegulatedTariffBundle(
        distributor="cez_distribuce",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        variable_components=(
            pricing.VariablePriceComponent(
                kind=pricing.PriceComponentKind.DISTRIBUTION,
                name="Distribuce",
                high_rate_czk_per_kwh=Decimal("1"),
                low_rate_czk_per_kwh=Decimal("0.5"),
                includes_vat=False,
            ),
            pricing.VariablePriceComponent(
                kind=pricing.PriceComponentKind.SYSTEM_SERVICES,
                name="Systémové služby",
                high_rate_czk_per_kwh=Decimal("0.1"),
                low_rate_czk_per_kwh=Decimal("0.1"),
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
                name="Daň",
                high_rate_czk_per_kwh=Decimal("0.0283"),
                low_rate_czk_per_kwh=Decimal("0.0283"),
                includes_vat=False,
            ),
        ),
        fixed_components=(
            pricing.FixedPriceComponent(
                kind=pricing.PriceComponentKind.BREAKER_FIXED,
                name="Jistič",
                monthly_czk=Decimal("200"),
                includes_vat=False,
            ),
            pricing.FixedPriceComponent(
                kind=pricing.PriceComponentKind.OTHER_FIXED,
                name=regulated.NON_NETWORK_INFRASTRUCTURE_COMPONENT_NAME,
                monthly_czk=Decimal("12.87"),
                includes_vat=False,
            ),
        ),
        source_url=source_url,
        checksum="a" * 64,
        confirmed=False,
    )
    evidence = (
        provenance.PriceEvidence(
            scope=sources.PRICE_SCOPE_REGULATED,
            source_name="ERÚ",
            document_name="fixture",
            source_url=source_url,
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
            checksum="a" * 64,
        ),
    )

    with pytest.raises(ValueError, match="confirmed bundles only"):
        catalog.ConfirmedRegulatedTariffVersion(bundle=bundle, evidence=evidence)


def test_append_is_idempotent_and_never_overwrites_historical_versions() -> None:
    modules = load_modules()
    catalog = modules[-1]
    first = _version_from_modules(
        modules,
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 6, 30),
        checksum="a" * 64,
    )
    second = _version_from_modules(
        modules,
        valid_from=date(2026, 7, 1),
        valid_to=date(2026, 12, 31),
        checksum="b" * 64,
    )
    options = {"unrelated": {"keep": True}}

    after_first = catalog.append_confirmed_regulated_tariff(options, first)
    same_again = catalog.append_confirmed_regulated_tariff(after_first, first)
    after_second = catalog.append_confirmed_regulated_tariff(same_again, second)

    assert same_again == after_first
    assert after_second["unrelated"] == {"keep": True}
    restored = catalog.confirmed_regulated_versions_from_options(after_second)
    assert restored == (first, second)
    assert first.fingerprint != second.fingerprint


def test_selection_requires_exact_context_and_uses_newest_applicable_version() -> None:
    modules = load_modules()
    catalog = modules[-1]
    annual = _version_from_modules(
        modules,
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        checksum="a" * 64,
    )
    update = _version_from_modules(
        modules,
        valid_from=date(2026, 8, 1),
        valid_to=date(2026, 12, 31),
        checksum="b" * 64,
    )
    options = catalog.append_confirmed_regulated_tariff({}, annual)
    options = catalog.append_confirmed_regulated_tariff(options, update)

    july = catalog.select_confirmed_regulated_tariff_for_day(
        options,
        distributor="cez_distribuce",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        day=date(2026, 7, 15),
    )
    august = catalog.select_confirmed_regulated_tariff_for_day(
        options,
        distributor="cez_distribuce",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        day=date(2026, 8, 14),
    )

    assert july.fingerprint == annual.fingerprint
    assert august.fingerprint == update.fingerprint
    with pytest.raises(LookupError, match="no confirmed"):
        catalog.select_confirmed_regulated_tariff_for_day(
            options,
            distributor="cez_distribuce",
            distribution_tariff="D25d",
            breaker_code="3x32A",
            day=date(2026, 8, 14),
        )


def test_equal_start_overlapping_versions_are_ambiguous_instead_of_silently_ranked() -> None:
    modules = load_modules()
    catalog = modules[-1]
    first = _version_from_modules(modules, checksum="a" * 64)
    second = _version_from_modules(modules, checksum="b" * 64)
    options = catalog.append_confirmed_regulated_tariff({}, first)
    options = catalog.append_confirmed_regulated_tariff(options, second)

    with pytest.raises(ValueError, match="ambiguous"):
        catalog.select_confirmed_regulated_tariff_for_day(
            options,
            distributor="cez_distribuce",
            distribution_tariff="D25d",
            breaker_code="3x25A",
            day=date(2026, 8, 14),
        )


def test_serialized_tampering_and_evidence_drift_fail_closed() -> None:
    catalog, version = _version()
    tampered = deepcopy(version.as_dict())
    tampered["bundle"]["variable_components"][0]["high_rate_czk_per_kwh"] = "99.99"
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        catalog.ConfirmedRegulatedTariffVersion.from_dict(tampered)

    wrong_evidence = deepcopy(version.as_dict())
    wrong_evidence["evidence"][0]["checksum"] = "c" * 64
    with pytest.raises(ValueError, match="checksum"):
        catalog.ConfirmedRegulatedTariffVersion.from_dict(wrong_evidence)


def test_duplicate_serialized_fingerprint_is_rejected_as_corrupt_options_state() -> None:
    catalog, version = _version()
    record = version.as_dict()
    with pytest.raises(ValueError, match="duplicate regulated tariff fingerprint"):
        catalog.confirmed_regulated_versions_from_options(
            {catalog.OPTION_CONFIRMED_REGULATED_TARIFFS: [record, deepcopy(record)]}
        )
