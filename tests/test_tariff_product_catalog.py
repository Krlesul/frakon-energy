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


def load_module():
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.providers",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.providers.cez_tariffs",
        "custom_components.frakon_energy.providers.eon_tariffs",
        "custom_components.frakon_energy.providers.pre_tariffs",
        "custom_components.frakon_energy.providers.mnd_tariffs",
        "custom_components.frakon_energy.tariff_product_catalog",
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

    _load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    _load(
        "custom_components.frakon_energy.providers.cez_tariffs",
        "custom_components/frakon_energy/providers/cez_tariffs.py",
    )
    _load(
        "custom_components.frakon_energy.providers.eon_tariffs",
        "custom_components/frakon_energy/providers/eon_tariffs.py",
    )
    _load(
        "custom_components.frakon_energy.providers.pre_tariffs",
        "custom_components/frakon_energy/providers/pre_tariffs.py",
    )
    _load(
        "custom_components.frakon_energy.providers.mnd_tariffs",
        "custom_components/frakon_energy/providers/mnd_tariffs.py",
    )
    return _load(
        "custom_components.frakon_energy.tariff_product_catalog",
        "custom_components/frakon_energy/tariff_product_catalog.py",
    )


def test_default_catalog_is_deduplicated_to_canonical_supplier_product_choices() -> None:
    catalog = load_module()

    options = catalog.default_tariff_product_options()

    assert len(options) == 15
    assert [(item.supplier, item.product_name, item.contract_kind) for item in options] == [
        ("cez", "Basic", "indefinite"),
        ("cez", "Elektřina bez závazku", "indefinite"),
        ("cez", "Elektřina na dobu neurčitou", "indefinite"),
        ("cez", "Elektřina pro ZTP", "indefinite"),
        ("cez", "eTarif", "indefinite"),
        ("cez", "Krátko odběr", "fixed"),
        ("cez", "Zelená elektřina", "indefinite"),
        ("eon", "Elektřina výhodně PRO na 3 roky", "fixed"),
        ("eon", "Variant PRO na 2 roky", "fixed"),
        ("pre", "PRE PROUD FAVORIT 2", "fixed"),
        ("pre", "PRE PROUD FAVORIT 3", "fixed"),
        ("pre", "PRE PROUD NEFIX", "indefinite"),
        ("mnd", "Proud - Ceník Říjen 28", "fixed"),
        ("mnd", "Proud - Domácnosti", "indefinite"),
        ("mnd", "Proud - Klesající ceník Duben 29", "fixed"),
    ]


def test_eon_and_pre_distribution_specific_documents_do_not_duplicate_wizard_products() -> None:
    catalog = load_module()
    options = catalog.default_tariff_product_options()

    eon = [item for item in options if item.supplier == "eon"]
    pre = [item for item in options if item.supplier == "pre"]

    assert len(eon) == 2
    assert len(pre) == 3
    assert len({(item.product_name, item.contract_kind) for item in eon}) == 2
    assert len({(item.product_name, item.contract_kind) for item in pre}) == 3


def test_mnd_products_explicitly_surface_dynamic_document_resolution() -> None:
    catalog = load_module()
    options = catalog.default_tariff_product_options()

    mnd = [item for item in options if item.supplier == "mnd"]
    static = [item for item in options if item.supplier != "mnd"]

    assert len(mnd) == 3
    assert all(item.source_resolution == "dynamic_resolver" for item in mnd)
    assert all(item.requires_document_resolver is True for item in mnd)
    assert all(item.source_resolution == "static_catalog" for item in static)
    assert all(item.requires_document_resolver is False for item in static)


def test_every_wizard_option_is_supplier_commercial_only() -> None:
    catalog = load_module()

    options = catalog.default_tariff_product_options()

    assert {item.price_scope for item in options} == {"supplier_commercial"}


def test_catalog_payload_is_json_safe_grouped_and_non_authoritative() -> None:
    catalog = load_module()

    payload = catalog.tariff_product_catalog_payload()

    assert [group["supplier"] for group in payload["suppliers"]] == [
        "cez",
        "eon",
        "pre",
        "mnd",
    ]
    assert [len(group["products"]) for group in payload["suppliers"]] == [7, 2, 3, 3]
    assert payload["price_scope"] == "supplier_commercial"
    assert payload["activation_performed"] is False
    assert all(
        isinstance(product["requires_document_resolver"], bool)
        for group in payload["suppliers"]
        for product in group["products"]
    )
