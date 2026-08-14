import asyncio
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
        "custom_components.frakon_energy.contracts",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components.frakon_energy.tariff_download",
        "custom_components.frakon_energy.tariff_fetch",
        "custom_components.frakon_energy.tariff_discovery",
        "custom_components.frakon_energy.tariff_selected_download",
        "custom_components.frakon_energy.tariff_adapter_registry",
        "custom_components.frakon_energy.tariff_http_ha",
        "custom_components.frakon_energy.tariff_selected_download_ha",
        "homeassistant",
        "homeassistant.core",
    )
    for name in names:
        sys.modules.pop(name, None)

    for name in ("custom_components", "custom_components.frakon_energy"):
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
    _load(
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components/frakon_energy/tariff_candidate_selection.py",
    )
    _load(
        "custom_components.frakon_energy.tariff_download",
        "custom_components/frakon_energy/tariff_download.py",
    )
    _load(
        "custom_components.frakon_energy.tariff_fetch",
        "custom_components/frakon_energy/tariff_fetch.py",
    )
    _load(
        "custom_components.frakon_energy.tariff_discovery",
        "custom_components/frakon_energy/tariff_discovery.py",
    )
    selected = _load(
        "custom_components.frakon_energy.tariff_selected_download",
        "custom_components/frakon_energy/tariff_selected_download.py",
    )

    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    sys.modules["homeassistant"] = homeassistant
    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:
        pass

    core.HomeAssistant = HomeAssistant
    sys.modules["homeassistant.core"] = core

    registry_module = types.ModuleType(
        "custom_components.frakon_energy.tariff_adapter_registry"
    )
    default_registry = sources.TariffAdapterRegistry()
    registry_calls = []

    def build_default_tariff_adapter_registry():
        registry_calls.append(True)
        return default_registry

    registry_module.build_default_tariff_adapter_registry = (
        build_default_tariff_adapter_registry
    )
    sys.modules[registry_module.__name__] = registry_module

    http_module = types.ModuleType("custom_components.frakon_energy.tariff_http_ha")
    http_calls = []
    http_sentinel = object()

    async def async_fetch_selected_tariff_document_ha(hass, **kwargs):
        http_calls.append((hass, kwargs))
        return http_sentinel

    http_module.async_fetch_selected_tariff_document_ha = (
        async_fetch_selected_tariff_document_ha
    )
    sys.modules[http_module.__name__] = http_module

    pure_calls = []
    pure_sentinel = object()

    async def fake_pure(contract, **kwargs):
        pure_calls.append((contract, kwargs))
        result = await kwargs["fetch_selected"](
            candidate="candidate",
            request="request",
            checked_at=kwargs["checked_at"],
        )
        assert result is http_sentinel
        return pure_sentinel

    selected.async_fetch_selected_contract_tariff = fake_pure
    wrapper = _load(
        "custom_components.frakon_energy.tariff_selected_download_ha",
        "custom_components/frakon_energy/tariff_selected_download_ha.py",
    )
    return (
        contracts,
        sources,
        wrapper,
        HomeAssistant,
        default_registry,
        registry_calls,
        http_calls,
        pure_calls,
        pure_sentinel,
    )


def _contract(contracts):
    return contracts.ElectricityContract(
        supplier=contracts.Supplier.CEZ,
        distributor=contracts.Distributor.CEZ_DISTRIBUCE,
        product_name="Basic",
        contract_kind=contracts.ContractKind.INDEFINITE,
        distribution_tariff="D25d",
        breaker=contracts.Breaker(phases=3, amperes=25),
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        customer_confirmed=False,
    )


def test_ha_wrapper_delegates_to_pure_orchestrator_and_http_adapter() -> None:
    (
        contracts,
        sources,
        wrapper,
        HomeAssistant,
        _default_registry,
        registry_calls,
        http_calls,
        pure_calls,
        pure_sentinel,
    ) = load_modules()
    hass = HomeAssistant()
    contract = _contract(contracts)
    registry = sources.TariffAdapterRegistry()
    checked_at = datetime(2026, 8, 14, 16, 45, tzinfo=timezone.utc)

    result = asyncio.run(
        wrapper.async_fetch_selected_contract_tariff_ha(
            hass,
            contract=contract,
            day=date(2026, 8, 14),
            selected_fingerprint="a" * 64,
            checked_at=checked_at,
            registry=registry,
        )
    )

    assert result is pure_sentinel
    assert registry_calls == []
    assert len(pure_calls) == 1
    called_contract, kwargs = pure_calls[0]
    assert called_contract is contract
    assert kwargs["registry"] is registry
    assert kwargs["day"] == date(2026, 8, 14)
    assert kwargs["selected_fingerprint"] == "a" * 64
    assert kwargs["checked_at"] == checked_at
    assert http_calls == [
        (
            hass,
            {
                "candidate": "candidate",
                "request": "request",
                "checked_at": checked_at,
            },
        )
    ]


def test_ha_wrapper_builds_canonical_registry_when_not_injected() -> None:
    (
        contracts,
        _sources,
        wrapper,
        HomeAssistant,
        default_registry,
        registry_calls,
        _http_calls,
        pure_calls,
        pure_sentinel,
    ) = load_modules()
    hass = HomeAssistant()
    contract = _contract(contracts)
    checked_at = datetime(2026, 8, 14, 16, 50, tzinfo=timezone.utc)

    result = asyncio.run(
        wrapper.async_fetch_selected_contract_tariff_ha(
            hass,
            contract=contract,
            day=date(2026, 8, 14),
            selected_fingerprint="b" * 64,
            checked_at=checked_at,
        )
    )

    assert result is pure_sentinel
    assert registry_calls == [True]
    assert pure_calls[0][1]["registry"] is default_registry
