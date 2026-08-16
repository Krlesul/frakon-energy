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


def load_module():
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.const",
        "custom_components.frakon_energy.contracts",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components.frakon_energy.tariff_discovery",
        "custom_components.frakon_energy.tariff_adapter_registry",
        "custom_components.frakon_energy.tariff_discovery_ws_api",
        "homeassistant",
        "homeassistant.components",
        "homeassistant.components.websocket_api",
        "homeassistant.core",
        "voluptuous",
    )
    for name in names:
        sys.modules.pop(name, None)

    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    const = types.ModuleType("custom_components.frakon_energy.const")
    const.DOMAIN = "frakon_energy"
    sys.modules[const.__name__] = const

    contracts = _load(
        "custom_components.frakon_energy.contracts",
        "custom_components/frakon_energy/contracts.py",
    )
    sources = _load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    selection = _load(
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components/frakon_energy/tariff_candidate_selection.py",
    )
    discovery = _load(
        "custom_components.frakon_energy.tariff_discovery",
        "custom_components/frakon_energy/tariff_discovery.py",
    )

    adapter_registry = types.ModuleType(
        "custom_components.frakon_energy.tariff_adapter_registry"
    )
    adapter_registry.build_default_tariff_adapter_registry = lambda: None
    adapter_registry.build_entry_tariff_adapter_registry = lambda _options: None
    sys.modules[adapter_registry.__name__] = adapter_registry

    vol = types.ModuleType("voluptuous")
    vol.Required = lambda key: key
    sys.modules["voluptuous"] = vol

    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    sys.modules["homeassistant"] = homeassistant

    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    sys.modules["homeassistant.components"] = components

    websocket_api = types.ModuleType("homeassistant.components.websocket_api")
    registered_commands = []

    class ActiveConnection:
        pass

    def websocket_command(_schema):
        def decorator(func):
            return func
        return decorator

    def async_response(func):
        return func

    def require_admin(func):
        async def wrapped(hass, connection, msg):
            legacy = getattr(connection, "require_admin", None)
            if legacy is not None:
                legacy()
            return await func(hass, connection, msg)
        return wrapped

    def async_register_command(_hass, command):
        registered_commands.append(command)

    websocket_api.ActiveConnection = ActiveConnection
    websocket_api.websocket_command = websocket_command
    websocket_api.async_response = async_response
    websocket_api.require_admin = require_admin
    websocket_api.async_register_command = async_register_command
    sys.modules["homeassistant.components.websocket_api"] = websocket_api
    components.websocket_api = websocket_api

    core = types.ModuleType("homeassistant.core")
    core.callback = lambda func: func

    class HomeAssistant:
        pass

    core.HomeAssistant = HomeAssistant
    sys.modules["homeassistant.core"] = core

    ws = _load(
        "custom_components.frakon_energy.tariff_discovery_ws_api",
        "custom_components/frakon_energy/tariff_discovery_ws_api.py",
    )
    return ws, contracts, sources, selection, discovery, registered_commands


class ConfigEntries:
    def __init__(self, entry):
        self.entry = entry
        self.lookup_calls = []

    def async_get_entry(self, entry_id):
        self.lookup_calls.append(entry_id)
        if self.entry is not None and self.entry.entry_id == entry_id:
            return self.entry
        return None


class Hass:
    def __init__(self, entry):
        self.data = {}
        self.config_entries = ConfigEntries(entry)


class Connection:
    def __init__(self):
        self.admin_calls = 0
        self.results = []
        self.errors = []

    def require_admin(self):
        self.admin_calls += 1

    def send_result(self, message_id, payload):
        self.results.append((message_id, payload))

    def send_error(self, message_id, code, message):
        self.errors.append((message_id, code, message))


def _entry(domain="frakon_energy", *, options=None):
    return types.SimpleNamespace(
        entry_id="entry-1",
        domain=domain,
        options={} if options is None else dict(options),
    )


def _contract_dict(contracts, *, supplier=None):
    contract = contracts.ElectricityContract(
        supplier=supplier or contracts.Supplier.CEZ,
        distributor=contracts.Distributor.CEZ_DISTRIBUCE,
        product_name="Basic" if supplier in (None, contracts.Supplier.CEZ) else "Unknown",
        contract_kind=contracts.ContractKind.INDEFINITE,
        distribution_tariff="D25d",
        breaker=contracts.Breaker(phases=3, amperes=25),
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        customer_confirmed=False,
    )
    return contract.as_dict()


class Adapter:
    supplier = "cez"
    official_domains = ("cez.cz",)
    catalog_index_url = "https://www.cez.cz/cs/nove-ceny"

    def __init__(self, sources):
        self.sources = sources
        self.calls = []

    async def async_discover(self, query):
        self.calls.append(query)
        return (
            self.sources.TariffDocumentCandidate(
                document=self.sources.OfficialTariffDocument(
                    supplier="cez",
                    source_url="https://www.cez.cz/file/verified.pdf",
                    discovered_at=datetime(2026, 8, 14, 14, 45, tzinfo=timezone.utc),
                    document_date=date(2026, 1, 1),
                    content_type="application/pdf",
                ),
                product_name="Basic",
                valid_from=date(2026, 1, 1),
                match_score=100,
                match_reasons=("exact verified test match",),
                price_scope=self.sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
            ),
        )


def test_valid_command_is_admin_only_and_returns_read_only_review_payload() -> None:
    ws, contracts, sources, _selection, _discovery, registered = load_module()
    entry = _entry()
    hass = Hass(entry)
    connection = Connection()
    registry = sources.TariffAdapterRegistry()
    adapter = Adapter(sources)
    registry.register(adapter)

    ws.async_register_tariff_discovery_websocket(hass, registry=registry)
    assert len(registered) == 1

    asyncio.run(
        registered[0](
            hass,
            connection,
            {
                "id": 7,
                "type": ws.COMMAND_TARIFF_DISCOVER,
                "entry_id": entry.entry_id,
                "contract": _contract_dict(contracts),
                "day": "2026-08-14",
            },
        )
    )

    assert connection.admin_calls == 1
    assert connection.errors == []
    assert len(connection.results) == 1
    message_id, payload = connection.results[0]
    assert message_id == 7
    assert payload["entry_id"] == "entry-1"
    assert payload["day"] == "2026-08-14"
    assert payload["supported_suppliers"] == ["cez"]
    assert payload["download_performed"] is False
    assert payload["parsing_performed"] is False
    assert payload["persistence_performed"] is False
    assert payload["activation_performed"] is False
    assert len(payload["contract_fingerprint"]) == 64
    assert len(payload["candidates"]) == 1
    candidate = payload["candidates"][0]
    assert candidate["supplier"] == "cez"
    assert candidate["product_name"] == "Basic"
    assert candidate["valid_from"] == "2026-01-01"
    assert candidate["valid_to"] is None
    assert candidate["document_date"] == "2026-01-01"
    assert candidate["match_reasons"] == ["exact verified test match"]
    assert candidate["download_performed"] is False
    assert candidate["activation_performed"] is False
    assert len(adapter.calls) == 1


def test_registration_is_idempotent_for_same_registry() -> None:
    ws, _contracts, sources, _selection, _discovery, registered = load_module()
    hass = Hass(_entry())
    registry = sources.TariffAdapterRegistry()
    registry.register(Adapter(sources))

    ws.async_register_tariff_discovery_websocket(hass, registry=registry)
    ws.async_register_tariff_discovery_websocket(hass, registry=registry)

    assert len(registered) == 1


def test_missing_or_wrong_domain_entry_returns_entry_not_found() -> None:
    ws, contracts, sources, _selection, _discovery, registered = load_module()
    registry = sources.TariffAdapterRegistry()
    registry.register(Adapter(sources))
    hass = Hass(_entry(domain="other_domain"))
    connection = Connection()
    ws.async_register_tariff_discovery_websocket(hass, registry=registry)

    asyncio.run(
        registered[0](
            hass,
            connection,
            {
                "id": 8,
                "type": ws.COMMAND_TARIFF_DISCOVER,
                "entry_id": "entry-1",
                "contract": _contract_dict(contracts),
                "day": "2026-08-14",
            },
        )
    )

    assert connection.admin_calls == 1
    assert connection.results == []
    assert connection.errors[0][1] == "entry_not_found"


def test_invalid_contract_and_day_fail_before_discovery() -> None:
    ws, contracts, sources, _selection, _discovery, registered = load_module()
    adapter = Adapter(sources)
    registry = sources.TariffAdapterRegistry()
    registry.register(adapter)
    hass = Hass(_entry())
    ws.async_register_tariff_discovery_websocket(hass, registry=registry)

    bad_contract = Connection()
    asyncio.run(
        registered[0](
            hass,
            bad_contract,
            {
                "id": 9,
                "type": ws.COMMAND_TARIFF_DISCOVER,
                "entry_id": "entry-1",
                "contract": {"schema_version": 999},
                "day": "2026-08-14",
            },
        )
    )
    assert bad_contract.errors[0][1] == "invalid_contract"

    bad_day = Connection()
    asyncio.run(
        registered[0](
            hass,
            bad_day,
            {
                "id": 10,
                "type": ws.COMMAND_TARIFF_DISCOVER,
                "entry_id": "entry-1",
                "contract": _contract_dict(contracts),
                "day": "not-a-date",
            },
        )
    )
    assert bad_day.errors[0][1] == "invalid_day"
    assert adapter.calls == []


def test_unsupported_supplier_returns_explicit_error_without_side_effects() -> None:
    ws, contracts, sources, _selection, _discovery, registered = load_module()
    registry = sources.TariffAdapterRegistry()
    registry.register(Adapter(sources))
    hass = Hass(_entry())
    connection = Connection()
    ws.async_register_tariff_discovery_websocket(hass, registry=registry)

    asyncio.run(
        registered[0](
            hass,
            connection,
            {
                "id": 11,
                "type": ws.COMMAND_TARIFF_DISCOVER,
                "entry_id": "entry-1",
                "contract": _contract_dict(contracts, supplier=contracts.Supplier.INNOGY),
                "day": "2026-08-14",
            },
        )
    )

    assert connection.results == []
    assert connection.errors[0][1] == "supplier_not_supported"


def test_registry_cannot_be_silently_replaced_after_registration() -> None:
    ws, _contracts, sources, _selection, _discovery, _registered = load_module()
    hass = Hass(_entry())
    first = sources.TariffAdapterRegistry()
    first.register(Adapter(sources))
    second = sources.TariffAdapterRegistry()
    second.register(Adapter(sources))

    ws.async_register_tariff_discovery_websocket(hass, registry=first)
    try:
        ws.async_register_tariff_discovery_websocket(hass, registry=second)
    except ValueError as err:
        assert "already configured" in str(err)
    else:
        raise AssertionError("Registry replacement must fail closed")


def test_entry_registry_uses_only_current_entry_options_for_default_runtime_registry() -> None:
    ws, _contracts, sources, _selection, _discovery, _registered = load_module()
    entry = _entry(options={"entry_marker": "entry-1"})
    hass = Hass(entry)
    base = sources.TariffAdapterRegistry()
    base.register(Adapter(sources))
    derived = sources.TariffAdapterRegistry()
    derived.register(Adapter(sources))
    hass.data = {
        "frakon_energy": {
            ws._REGISTRY_KEY: base,
            ws._REGISTRY_EXPLICIT_KEY: False,
        }
    }
    calls = []

    def build_entry(options):
        calls.append(dict(options))
        return derived

    ws.build_entry_tariff_adapter_registry = build_entry

    result = ws._registry_for_entry(hass, entry, registry=base)

    assert result is derived
    assert calls == [{"entry_marker": "entry-1"}]


def test_explicit_or_hot_reload_registry_is_never_replaced_by_entry_options() -> None:
    ws, _contracts, sources, _selection, _discovery, _registered = load_module()
    entry = _entry(options={"entry_marker": "must-not-be-read"})
    hass = Hass(entry)
    base = sources.TariffAdapterRegistry()
    base.register(Adapter(sources))
    calls = []
    ws.build_entry_tariff_adapter_registry = lambda options: calls.append(options)

    ws.async_register_tariff_discovery_websocket(hass, registry=base)
    assert ws._registry_for_entry(hass, entry, registry=base) is base
    assert calls == []

    hot_reload_hass = Hass(entry)
    hot_reload_hass.data = {"frakon_energy": {ws._REGISTRY_KEY: base}}
    assert ws._registry_for_hass(hot_reload_hass) is base
    assert hot_reload_hass.data["frakon_energy"][ws._REGISTRY_EXPLICIT_KEY] is True
    assert ws._registry_for_entry(hot_reload_hass, entry, registry=base) is base
    assert calls == []
