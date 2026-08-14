from datetime import date
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
    )
    for name in names:
        sys.modules.pop(name, None)

    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    def load(name: str, path: str):
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
    regulated = load(
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
    return pricing, sources, provenance, regulated, cz, assembly, catalog


def _eru(cz):
    return cz.RegulatedPriceSource(
        authority=cz.RegulatedAuthority.ERU,
        document_id="Cenový výměr 14/2025",
        source_url="https://eru.gov.cz/energeticky-regulacni-vestnik-182025",
        valid_from=date(2026, 1, 1),
    )


def _ote(cz):
    return cz.RegulatedPriceSource(
        authority=cz.RegulatedAuthority.OTE,
        document_id="OTE 2026",
        source_url="https://www.ote-cr.cz/cs/registrace-a-smlouvy/smluvni-vztahy-elektrina/ceny-za-sluzby-ote",
        valid_from=date(2026, 1, 1),
    )


def _regulated_inputs(cz):
    return cz.CzechRegulatedTariffInputs(
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
        sources=(_eru(cz), _ote(cz)),
    )


def _assembly(modules, *, product: str = "Basic", commercial_from: date = date(2026, 1, 1)):
    pricing, sources, provenance, _, cz, assembly, _ = modules
    inputs = _regulated_inputs(cz)
    regulated = inputs.to_bundle(confirmed=True)
    supplier_evidence = provenance.PriceEvidence(
        scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
        source_name="ČEZ Prodej",
        document_name=f"{product} 2026",
        source_url=f"https://www.cez.cz/file/edee/{product.lower()}-2026.pdf",
        valid_from=commercial_from,
        checksum=("a" if product == "Basic" else "c") * 64,
    )
    evidence = provenance.MultiSourceTariffProvenance(
        (supplier_evidence, *inputs.regulated_evidence())
    )
    commodity = pricing.VariablePriceComponent(
        pricing.PriceComponentKind.COMMODITY,
        f"ČEZ {product} commodity",
        Decimal("3.960") if product == "Basic" else Decimal("3.500"),
        Decimal("3.700") if product == "Basic" else Decimal("3.300"),
    )
    supplier_fixed = pricing.FixedPriceComponent(
        pricing.PriceComponentKind.SUPPLIER_FIXED,
        f"ČEZ {product} stálá platba",
        Decimal("130.68"),
    )
    return assembly.assemble_all_in_tariff(
        supplier="ČEZ",
        product_name=product,
        distribution_tariff="D25d",
        breaker_code="3x25A",
        commercial_valid_from=commercial_from,
        commodity=commodity,
        supplier_fixed=supplier_fixed,
        regulated=regulated,
        provenance=evidence,
    )


def test_all_in_catalog_round_trip_preserves_complete_assembly_and_provenance() -> None:
    modules = load_modules()
    *_, catalog = modules
    assembly = _assembly(modules)
    item = catalog.PersistedAllInTariff(assembly=assembly, confirmed=False)

    payload = item.as_dict()
    assert payload["schema_version"] == catalog.ALL_IN_CATALOG_SCHEMA_VERSION
    restored = catalog.PersistedAllInTariff.from_dict(payload)

    assert restored == item
    assert restored.assembly.all_in_ready is True
    assert restored.assembly.provenance == assembly.provenance
    assert restored.assembly.all_in_vt_czk_kwh == assembly.all_in_vt_czk_kwh


def test_append_preserves_options_history_and_is_idempotent() -> None:
    modules = load_modules()
    *_, catalog = modules
    assembly = _assembly(modules)

    options = {"unrelated": {"keep": True}, "tariff_catalog": ["legacy-stays"]}
    once = catalog.append_all_in_tariff(options, assembly)
    twice = catalog.append_all_in_tariff(once, assembly)

    assert twice == once
    assert twice["unrelated"] == {"keep": True}
    assert twice["tariff_catalog"] == ["legacy-stays"]
    stored = catalog.all_in_tariffs_from_options(twice)
    assert len(stored) == 1
    assert stored[0].confirmed is False
    assert stored[0].assembly == assembly


def test_confirmation_preserves_identity_and_controls_active_selection() -> None:
    modules = load_modules()
    *_, catalog = modules
    old = _assembly(modules, product="Basic", commercial_from=date(2026, 1, 1))
    new = _assembly(modules, product="eTarif", commercial_from=date(2026, 6, 1))

    options = catalog.append_all_in_tariff({}, old)
    options = catalog.append_all_in_tariff(options, new)
    stored = catalog.all_in_tariffs_from_options(options)
    old_fingerprint = catalog.all_in_tariff_fingerprint(stored[0])
    new_fingerprint = catalog.all_in_tariff_fingerprint(stored[1])

    options = catalog.confirm_all_in_tariff(options, old_fingerprint)
    stored = catalog.all_in_tariffs_from_options(options)
    assert stored[0].confirmed is True
    assert stored[1].confirmed is False
    assert catalog.all_in_tariff_fingerprint(stored[0]) == old_fingerprint
    assert catalog.confirmed_all_in_tariff_from_options(
        options, date(2026, 8, 14)
    ).assembly.product_name == "Basic"

    options = catalog.confirm_all_in_tariff(options, new_fingerprint)
    stored = catalog.all_in_tariffs_from_options(options)
    assert catalog.all_in_tariff_fingerprint(stored[1]) == new_fingerprint
    assert catalog.confirmed_all_in_tariff_from_options(
        options, date(2026, 8, 14)
    ).assembly.product_name == "eTarif"


def test_catalog_rejects_duplicate_identity_unknown_confirmation_and_corrupt_schema() -> None:
    modules = load_modules()
    *_, catalog = modules
    item = catalog.PersistedAllInTariff(assembly=_assembly(modules))
    duplicate = {
        catalog.OPTION_ALL_IN_TARIFF_CATALOG: [item.as_dict(), item.as_dict()]
    }
    try:
        catalog.all_in_tariffs_from_options(duplicate)
    except ValueError as err:
        assert "duplicate all-in tariff fingerprint" in str(err)
    else:
        raise AssertionError("Duplicate all-in identity must be rejected")

    try:
        catalog.confirm_all_in_tariff({}, "0" * 64)
    except LookupError:
        pass
    else:
        raise AssertionError("Unknown all-in confirmation target must be rejected")

    payload = item.as_dict()
    payload["schema_version"] = 999
    try:
        catalog.PersistedAllInTariff.from_dict(payload)
    except ValueError as err:
        assert "unsupported all-in tariff catalog schema version" in str(err)
    else:
        raise AssertionError("Unknown all-in catalog schema must be rejected")


def test_catalog_reload_reapplies_all_in_completeness_gate() -> None:
    modules = load_modules()
    pricing, _, _, _, _, _, catalog = modules
    item = catalog.PersistedAllInTariff(assembly=_assembly(modules))
    payload = item.as_dict()
    variable = payload["assembly"]["variable_components"]
    payload["assembly"]["variable_components"] = [
        component
        for component in variable
        if component["kind"] != pricing.PriceComponentKind.ELECTRICITY_TAX.value
    ]

    try:
        catalog.PersistedAllInTariff.from_dict(payload)
    except ValueError as err:
        assert "electricity_tax" in str(err)
    else:
        raise AssertionError("Corrupt incomplete persisted all-in tariff must be rejected")
