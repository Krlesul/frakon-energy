import asyncio
from datetime import date
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
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components.frakon_energy.tariff_discovery",
        "custom_components.frakon_energy.tariff_discovery_ws_api",
        "custom_components.frakon_energy.tariff_download",
        "custom_components.frakon_energy.tariff_fetch",
        "custom_components.frakon_energy.tariff_http_ha",
        "custom_components.frakon_energy.tariff_parser_preview",
        "custom_components.frakon_energy.tariff_pdf_text",
        "custom_components.frakon_energy.tariff_parse_preview_ws_api",
        "homeassistant",
        "homeassistant.components",
        "homeassistant.components.websocket_api",
        "homeassistant.core",
        "homeassistant.util",
        "homeassistant.util.dt",
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

    selection_calls = []
    selection = types.ModuleType(
        "custom_components.frakon_energy.tariff_candidate_selection"
    )

    def select_tariff_candidate(*args, **kwargs):
        selection_calls.append((args, kwargs))
        raise AssertionError("unsupported supplier must stop before candidate selection")

    selection.select_tariff_candidate = select_tariff_candidate
    sys.modules[selection.__name__] = selection

    discovery_calls = []
    discovery = types.ModuleType("custom_components.frakon_energy.tariff_discovery")

    async def async_discover_contract_tariff_candidates(*args, **kwargs):
        discovery_calls.append((args, kwargs))
        raise AssertionError("unsupported supplier must stop before discovery")

    discovery.async_discover_contract_tariff_candidates = (
        async_discover_contract_tariff_candidates
    )
    sys.modules[discovery.__name__] = discovery

    discovery_ws = types.ModuleType(
        "custom_components.frakon_energy.tariff_discovery_ws_api"
    )
    registry_calls = []

    def _registry_for_hass(hass):
        registry_calls.append(hass)
        return object()

    discovery_ws._registry_for_hass = _registry_for_hass
    sys.modules[discovery_ws.__name__] = discovery_ws

    download = types.ModuleType("custom_components.frakon_energy.tariff_download")

    class ValidatedTariffDownload:
        pass

    download.ValidatedTariffDownload = ValidatedTariffDownload
    sys.modules[download.__name__] = download

    fetch = types.ModuleType("custom_components.frakon_energy.tariff_fetch")

    class TariffNotModified:
        pass

    fetch.TariffNotModified = TariffNotModified
    fetch.build_tariff_fetch_request = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("unsupported supplier must stop before fetch request")
    )
    sys.modules[fetch.__name__] = fetch

    http_calls = []
    http = types.ModuleType("custom_components.frakon_energy.tariff_http_ha")

    async def async_fetch_selected_tariff_document_ha(*args, **kwargs):
        http_calls.append((args, kwargs))
        raise AssertionError("unsupported supplier must stop before HTTP")

    http.async_fetch_selected_tariff_document_ha = async_fetch_selected_tariff_document_ha
    sys.modules[http.__name__] = http

    parser = types.ModuleType("custom_components.frakon_energy.tariff_parser_preview")

    class SupplierTariffParsePreview:
        pass

    parser.SupplierTariffParsePreview = SupplierTariffParsePreview
    parser.parse_supplier_tariff_preview = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("unsupported supplier must stop before parser")
    )
    parser.supplier_parser_supported = lambda supplier: getattr(supplier, "value", supplier) in {
        "cez",
        "eon",
        "pre",
    }
    sys.modules[parser.__name__] = parser

    pdf = types.ModuleType("custom_components.frakon_energy.tariff_pdf_text")
    pdf.extract_validated_tariff_pdf_text = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("unsupported supplier must stop before PDF extraction")
    )
    sys.modules[pdf.__name__] = pdf

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
    registered = []

    class ActiveConnection:
        pass

    def websocket_command(_schema):
        return lambda func: func

    websocket_api.ActiveConnection = ActiveConnection
    websocket_api.websocket_command = websocket_command
    def require_admin(func):
        async def wrapped(hass, connection, msg):
            legacy = getattr(connection, "require_admin", None)
            if legacy is not None:
                legacy()
            return await func(hass, connection, msg)
        return wrapped

    websocket_api.require_admin = require_admin
    websocket_api.async_response = lambda func: func
    websocket_api.async_register_command = lambda _hass, command: registered.append(command)
    sys.modules[websocket_api.__name__] = websocket_api
    components.websocket_api = websocket_api

    core = types.ModuleType("homeassistant.core")
    core.callback = lambda func: func

    class HomeAssistant:
        pass

    core.HomeAssistant = HomeAssistant
    sys.modules[core.__name__] = core

    util = types.ModuleType("homeassistant.util")
    util.__path__ = []
    sys.modules[util.__name__] = util
    dt = types.ModuleType("homeassistant.util.dt")
    dt.now = lambda: None
    sys.modules[dt.__name__] = dt
    util.dt = dt

    ws = _load(
        "custom_components.frakon_energy.tariff_parse_preview_ws_api",
        "custom_components/frakon_energy/tariff_parse_preview_ws_api.py",
    )
    return ws, contracts, registered, registry_calls, discovery_calls, selection_calls, http_calls


class ConfigEntries:
    def __init__(self, entry):
        self.entry = entry

    def async_get_entry(self, entry_id):
        return self.entry if entry_id == self.entry.entry_id else None


class Hass:
    def __init__(self, entry):
        self.data = {"frakon_energy": {}}
        self.config_entries = ConfigEntries(entry)
        self.executor_calls = []

    async def async_add_executor_job(self, target, *args):
        self.executor_calls.append((target, args))
        raise AssertionError("unsupported supplier must stop before executor")


class Connection:
    def __init__(self):
        self.errors = []
        self.results = []
        self.admin_calls = 0

    def require_admin(self):
        self.admin_calls += 1

    def send_error(self, message_id, code, message):
        self.errors.append((message_id, code, message))

    def send_result(self, message_id, payload):
        self.results.append((message_id, payload))


def test_unsupported_supplier_fails_before_discovery_http_or_executor() -> None:
    ws, contracts, registered, registry_calls, discovery_calls, selection_calls, http_calls = (
        load_module()
    )
    entry = types.SimpleNamespace(entry_id="entry-1", domain="frakon_energy")
    hass = Hass(entry)
    connection = Connection()
    contract = contracts.ElectricityContract(
        supplier=contracts.Supplier.MND,
        distributor=contracts.Distributor.CEZ_DISTRIBUCE,
        product_name="MND fixture",
        contract_kind=contracts.ContractKind.INDEFINITE,
        distribution_tariff="D25d",
        breaker=contracts.Breaker(phases=3, amperes=25),
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        customer_confirmed=False,
    )

    ws.async_register_tariff_parse_preview_websocket(hass)
    asyncio.run(
        registered[0](
            hass,
            connection,
            {
                "id": 91,
                "type": ws.COMMAND_TARIFF_PARSE_PREVIEW,
                "entry_id": "entry-1",
                "contract": contract.as_dict(),
                "day": "2026-08-14",
                "candidate_fingerprint": "0" * 64,
            },
        )
    )

    assert connection.admin_calls == 1
    assert connection.results == []
    assert connection.errors == [
        (91, "parser_not_supported", "supplier parser preview is not implemented: mnd")
    ]
    assert registry_calls == [hass]
    assert discovery_calls == []
    assert selection_calls == []
    assert http_calls == []
    assert hass.executor_calls == []
