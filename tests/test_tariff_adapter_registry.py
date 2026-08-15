from datetime import date, datetime, timezone
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
        "custom_components.frakon_energy.providers",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.providers.cez_tariffs",
        "custom_components.frakon_energy.providers.eon_tariffs",
        "custom_components.frakon_energy.providers.pre_tariffs",
        "custom_components.frakon_energy.providers.mnd_tariffs",
        "custom_components.frakon_energy.providers.mnd_confirmed_source_resolver",
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
    confirmed = _load(
        "custom_components.frakon_energy.providers.mnd_confirmed_source_resolver",
        "custom_components/frakon_energy/providers/mnd_confirmed_source_resolver.py",
    )
    registry_module = _load(
        "custom_components.frakon_energy.tariff_adapter_registry",
        "custom_components/frakon_energy/tariff_adapter_registry.py",
    )
    return sources, cez, eon, pre, mnd, confirmed, registry_module


def _query(
    sources,
    *,
    supplier: str,
    product_name: str,
    distributor: str,
    contract_kind: str,
    postcode: str | None = None,
):
    return sources.TariffSourceQuery(
        supplier=supplier,
        product_name=product_name,
        distributor=distributor,
        contract_kind=contract_kind,
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_on=date(2026, 8, 14),
        source_context=sources.TariffSourceResolutionContext(postcode=postcode),
    )


def _clock():
    return datetime(2026, 8, 14, 14, 0, tzinfo=timezone.utc)


def _confirmed_mnd_options(sources, confirmed, *, postcode: str = "41201"):
    context_fingerprint = sources.tariff_source_context_fingerprint(
        sources.TariffSourceResolutionContext(postcode=postcode)
    )
    resolution = confirmed.ConfirmedMndSourceResolution(
        source_context_fingerprint=context_fingerprint,
        product_name="Proud - Ceník Říjen 28",
        distributor="cez_distribuce",
        contract_kind="fixed",
        source_url=(
            "https://prod.mnd.cz/documents/view/"
            "12345678-1234-4234-8234-123456789abc"
        ),
        valid_from=date(2026, 6, 11),
        valid_to=date(2028, 10, 31),
        document_date=date(2026, 6, 11),
        document_sha256="a" * 64,
        confirmed_at=datetime(2026, 8, 14, 13, 0, tzinfo=timezone.utc),
    )
    return {
        confirmed.MND_CONFIRMED_SOURCE_RESOLUTIONS_OPTION: [resolution.as_dict()]
    }


def test_default_registry_contains_exact_mandatory_supplier_set() -> None:
    _sources, _cez, _eon, _pre, _mnd, _confirmed, registry_module = load_modules()

    registry = registry_module.build_default_tariff_adapter_registry(clock=_clock)

    assert registry.supported_suppliers() == ("cez", "eon", "mnd", "pre")
    assert type(registry.for_supplier("cez")).__name__ == "CezTariffCatalogAdapter"
    assert type(registry.for_supplier("eon")).__name__ == "EonTariffCatalogAdapter"
    assert type(registry.for_supplier("pre")).__name__ == "PreTariffCatalogAdapter"
    assert type(registry.for_supplier("mnd")).__name__ == "MndTariffCatalogAdapter"


def test_default_registry_discovers_verified_cez_eon_and_pre_candidates() -> None:
    sources, _cez, _eon, _pre, _mnd, _confirmed, registry_module = load_modules()
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
    sources, _cez, _eon, _pre, _mnd, _confirmed, registry_module = load_modules()
    registry = registry_module.build_default_tariff_adapter_registry(clock=_clock)

    result = __import__("asyncio").run(
        registry.async_discover_verified(
            _query(
                sources,
                supplier="mnd",
                product_name="Proud - Ceník Říjen 28",
                distributor="cez_distribuce",
                contract_kind="fixed",
                postcode="41201",
            )
        )
    )

    assert result == ()


def test_default_registry_injects_exact_mnd_resolver_when_available() -> None:
    sources, _cez, _eon, _pre, mnd, _confirmed, registry_module = load_modules()

    class Resolver:
        async def async_resolve(self, query, product):
            assert query.source_context.postcode == "41201"
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
                postcode="41201",
            )
        )
    )

    assert len(result) == 1
    assert result[0].document.supplier == "mnd"
    assert result[0].price_scope == sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL
    assert result[0].document.source_url.startswith(
        "https://prod.mnd.cz/documents/view/"
    )


def test_entry_registry_uses_only_confirmed_sources_from_that_entry_options() -> None:
    sources, _cez, _eon, _pre, _mnd, confirmed, registry_module = load_modules()
    authorized_options = _confirmed_mnd_options(sources, confirmed, postcode="41201")

    authorized = registry_module.build_entry_tariff_adapter_registry(
        authorized_options,
        clock=_clock,
    )
    other_entry = registry_module.build_entry_tariff_adapter_registry(
        {},
        clock=_clock,
    )
    query = _query(
        sources,
        supplier="mnd",
        product_name="Proud - Ceník Říjen 28",
        distributor="cez_distribuce",
        contract_kind="fixed",
        postcode="41201",
    )

    authorized_candidates = __import__("asyncio").run(
        authorized.async_discover_verified(query)
    )
    other_candidates = __import__("asyncio").run(
        other_entry.async_discover_verified(query)
    )

    assert len(authorized_candidates) == 1
    assert authorized_candidates[0].document.sha256 == "a" * 64
    assert authorized_candidates[0].document.source_url.startswith(
        "https://prod.mnd.cz/documents/view/"
    )
    assert other_candidates == ()


def test_entry_registry_does_not_match_confirmed_source_for_another_context() -> None:
    sources, _cez, _eon, _pre, _mnd, confirmed, registry_module = load_modules()
    options = _confirmed_mnd_options(sources, confirmed, postcode="11000")
    registry = registry_module.build_entry_tariff_adapter_registry(options, clock=_clock)

    candidates = __import__("asyncio").run(
        registry.async_discover_verified(
            _query(
                sources,
                supplier="mnd",
                product_name="Proud - Ceník Říjen 28",
                distributor="cez_distribuce",
                contract_kind="fixed",
                postcode="41201",
            )
        )
    )

    assert candidates == ()


def test_entry_registry_rejects_corrupt_confirmed_resolution_options() -> None:
    _sources, _cez, _eon, _pre, _mnd, confirmed, registry_module = load_modules()
    corrupt = {
        confirmed.MND_CONFIRMED_SOURCE_RESOLUTIONS_OPTION: [
            {"schema_version": 999}
        ]
    }
    with pytest.raises(ValueError, match="unsupported confirmed MND source resolution"):
        registry_module.build_entry_tariff_adapter_registry(corrupt, clock=_clock)
