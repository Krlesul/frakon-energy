import asyncio
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
        "custom_components.frakon_energy.tariff_product_catalog",
        "custom_components.frakon_energy.tariff_product_catalog_ws_api",
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

    catalog = types.ModuleType("custom_components.frakon_energy.tariff_product_catalog")
    catalog.tariff_product_catalog_payload = lambda: {
        "suppliers": [
            {
                "supplier": "cez",
                "products": [
                    {
                        "supplier": "cez",
                        "product_name": "Basic",
                        "contract_kind": "indefinite",
                        "source_resolution": "static_catalog",
                        "requires_document_resolver": False,
                        "price_scope": "supplier_commercial",
                    }
                ],
            },
            {
                "supplier": "mnd",
                "products": [
                    {
                        "supplier": "mnd",
                        "product_name": "Proud - Ceník Říjen 28",
                        "contract_kind": "fixed",
                        "source_resolution": "dynamic_resolver",
                        "requires_document_resolver": True,
                        "price_scope": "supplier_commercial",
                    }
                ],
            },
        ],
        "price_scope": "supplier_commercial",
        "activation_performed": False,
    }
    sys.modules[catalog.__name__] = catalog

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

    websocket_api.ActiveConnection = ActiveConnection
    websocket_api.websocket_command = lambda _schema: (lambda func: func)
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
    sys.modules["homeassistant.components.websocket_api"] = websocket_api
    components.websocket_api = websocket_api

    core = types.ModuleType("homeassistant.core")
    core.callback = lambda func: func
    core.HomeAssistant = type("HomeAssistant", (), {})
    sys.modules["homeassistant.core"] = core

    ws = _load(
        "custom_components.frakon_energy.tariff_product_catalog_ws_api",
        "custom_components/frakon_energy/tariff_product_catalog_ws_api.py",
    )
    return ws, registered


class Hass:
    def __init__(self, entry):
        self.data = {}
        self.config_entries = types.SimpleNamespace(
            async_get_entry=lambda entry_id: entry if entry and entry.entry_id == entry_id else None
        )


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


def test_catalog_command_returns_read_only_canonical_products() -> None:
    ws, registered = load_module()
    entry = types.SimpleNamespace(entry_id="entry-1", domain="frakon_energy")
    hass = Hass(entry)
    connection = Connection()

    ws.async_register_tariff_product_catalog_websocket(hass)
    asyncio.run(
        registered[0](
            hass,
            connection,
            {
                "id": 21,
                "type": ws.COMMAND_TARIFF_PRODUCT_CATALOG,
                "entry_id": "entry-1",
            },
        )
    )

    assert connection.admin_calls == 1
    assert connection.errors == []
    assert len(connection.results) == 1
    message_id, payload = connection.results[0]
    assert message_id == 21
    assert payload["entry_id"] == "entry-1"
    assert [item["supplier"] for item in payload["suppliers"]] == ["cez", "mnd"]
    assert payload["suppliers"][0]["products"][0]["product_name"] == "Basic"
    assert payload["suppliers"][1]["products"][0]["requires_document_resolver"] is True
    assert payload["price_scope"] == "supplier_commercial"
    assert payload["download_performed"] is False
    assert payload["parsing_performed"] is False
    assert payload["persistence_performed"] is False
    assert payload["activation_performed"] is False


def test_catalog_registration_is_idempotent() -> None:
    ws, registered = load_module()
    hass = Hass(types.SimpleNamespace(entry_id="entry-1", domain="frakon_energy"))

    ws.async_register_tariff_product_catalog_websocket(hass)
    ws.async_register_tariff_product_catalog_websocket(hass)

    assert len(registered) == 1


def test_catalog_command_rejects_non_frakon_entry() -> None:
    ws, registered = load_module()
    hass = Hass(types.SimpleNamespace(entry_id="entry-1", domain="other"))
    connection = Connection()
    ws.async_register_tariff_product_catalog_websocket(hass)

    asyncio.run(
        registered[0](
            hass,
            connection,
            {
                "id": 22,
                "type": ws.COMMAND_TARIFF_PRODUCT_CATALOG,
                "entry_id": "entry-1",
            },
        )
    )

    assert connection.admin_calls == 1
    assert connection.results == []
    assert connection.errors[0][1] == "entry_not_found"
