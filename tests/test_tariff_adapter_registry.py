from datetime import date, datetime, timezone
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
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.providers.cez_tariffs",
        "custom_components.frakon_energy.providers.eon_tariffs",
        "custom_components.frakon_energy.providers.pre_tariffs",
        "custom_components.frakon_energy.providers.mnd_tariffs",
        "custom_components.frakon_energy.tariff_adapter_registry",
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

    sources = _load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    cez = _load(
        "custom_components.frakon_energy.providers.cez_tariffs",
        "custom_components/frakon_energy/providers/cez_tariffs.py",
    )
    eon = _load(
        "custom_components.frakon_energy.providers.eon_tariffs",
        "custom_components/frakon_energy/providers/eon_tariffs.py",
    )
    pre = _load(
        "custom_components.frakon_energy.providers.pre_tariffs",
        "custom_components/frakon_energy/providers/pre_tariffs.py",
    )
    mnd = _load(
        "custom_components.frakon_energy.providers.mnd_tariffs",
        "custom_components/frakon_energy/providers/mnd_tariffs.py",
    )
    registry_module = _load(
        "custom_components.frakon_energy.tariff_adapter_registry",
        "custom_components/frakon_energy/tariff_adapter_registry.py",
    )
    return sources, cez, eon, pre, mnd, registry_module


def _query(
    sources,
    *,
    supplier: str,
    product_name: str,
    distributor: str,
    contract_kind: str,
):
    return sources.TariffSourceQuery(
        supplier=supplier,
        product_name=product_name,
        distributor=distributor,
        contract_kind=contract_kind,
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_on=date(2026, 8, 14),
    )


def _clock():
    return datetime(2026, 8, 14, 14, 0, tzinfo=timezone.utc)


def test_default_registry_contains_exact_mandatory_supplier_set() -> None:
    _sources, _cez, _eon, _pre, _mnd, registry_module = load_modules()

    registry = registry_module.build_default_tariff_adapter_registry(clock=_clock)

    assert registry.supported_suppliers() == ("cez", "eon", "mnd", "pre")
    assert type(registry.for_supplier("cez")).__name__ == "CezTariffCatalogAdapter"
    assert type(registry.for_supplier("eon")).__name__ == "EonTariffCatalogAdapter"
    assert type(registry.for_supplier("pre")).__name__ == "PreTariffCatalogAdapter"
    assert type(registry.for_supplier("mnd")).__name__ == "MndTariffCatalogAdapter"


def test_default_registry_discovers_verified_cez_eon_and_pre_candidates() -> None:
    sources, _cez, _eon, _pre, _mnd, registry_module = load_modules()
    registry = registry_module.build_default_tariff_adapter_registry(clock=_clock)

    cez = __import__("asyncio").run(
        registry.async_discover_verified(
            _query(
                sources,
                supplier="cez",
                product_name="Basic",
                distributor="cez_distribuce",
                contract_kind="indefinite",
            )
        )
    )
    eon = __import__("asyncio").run(
        registry.async_discover_verified(
            _query(
                sources,
                supplier="eon",
                product_name="Variant PRO na 2 roky",
                distributor="eg_d",
                contract_kind="fixed",
            )
        )
    )
    pre = __import__("asyncio").run(
        registry.async_discover_verified(
            _query(
                sources,
                supplier="pre",
                product_name="PRE PROUD FAVORIT 2",
                distributor="pre_distribuce",
                contract_kind="fixed",
            )
        )
    )

    assert len(cez) == len(eon) == len(pre) == 1
    assert cez[0].document.source_url.startswith("https://www.cez.cz/")
    assert eon[0].document.source_url.startswith("https://www.eon.cz/")
    assert pre[0].document.source_url.startswith("https://www.pre.cz/")
    assert {cez[0].price_scope, eon[0].price_scope, pre[0].price_scope} == {
        sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL
    }
    assert cez[0].document.discovered_at == _clock()
    assert eon[0].document.discovered_at == _clock()
    assert pre[0].document.discovered_at == _clock()


def test_mnd_is_registered_but_fails_closed_without_document_resolver() -> None:
    sources, _cez, _eon, _pre, _mnd, registry_module = load_modules()
    registry = registry_module.build_default_tariff_adapter_registry(clock=_clock)

    result = __import__("asyncio").run(
        registry.async_discover_verified(
            _query(
                sources,
                supplier="mnd",
                product_name="Proud - Ceník Říjen 28",
                distributor="cez_distribuce",
                contract_kind="fixed",
            )
        )
    )

    assert result == ()


def test_default_registry_injects_exact_mnd_resolver_when_available() -> None:
    sources, _cez, _eon, _pre, mnd, registry_module = load_modules()

    class Resolver:
        async def async_resolve(self, query, product):
            return mnd.MndResolvedTariffSource(
                product_name=product.product_name,
                distributor=query.distributor,
                contract_kind=product.contract_kind,
                source_url=(
                    "https://prod.mnd.cz/documents/view/"
                    "12345678-1234-4234-8234-123456789abc"
                ),
                valid_from=date(2026, 6, 11),
                valid_to=date(2028, 10, 31),
                document_date=date(2026, 6, 11),
                discovered_at=_clock(),
            )

    registry = registry_module.build_default_tariff_adapter_registry(
        mnd_resolver=Resolver(),
        clock=_clock,
    )
    result = __import__("asyncio").run(
        registry.async_discover_verified(
            _query(
                sources,
                supplier="mnd",
                product_name="Proud - Ceník Říjen 28",
                distributor="cez_distribuce",
                contract_kind="fixed",
            )
        )
    )

    assert len(result) == 1
    assert result[0].document.supplier == "mnd"
    assert result[0].price_scope == sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL
    assert result[0].document.source_url.startswith(
        "https://prod.mnd.cz/documents/view/"
    )
