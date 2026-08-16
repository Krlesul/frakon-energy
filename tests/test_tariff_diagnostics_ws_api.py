import asyncio
from dataclasses import dataclass
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


def load_module(*, diagnostics_mode="success"):
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.const",
        "custom_components.frakon_energy.tariff_diagnostics",
        "custom_components.frakon_energy.tariff_diagnostics_ws_api",
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

    calls = []
    const = types.ModuleType("custom_components.frakon_energy.const")
    const.DOMAIN = "frakon_energy"
    sys.modules[const.__name__] = const

    diagnostics = types.ModuleType("custom_components.frakon_energy.tariff_diagnostics")

    @dataclass(frozen=True)
    class Snapshot:
        diagnostic_day: date

        def as_dict(self):
            return {
                "day": self.diagnostic_day.isoformat(),
                "authority_method": "verified_parser",
                "supplier_source": {
                    "source_url": "https://www.cez.cz/cenik.pdf",
                    "document_date": "2026-01-01",
                    "checksum": "f" * 64,
                },
                "parser": {
                    "supported": True,
                    "status": "verified_parser_active",
                },
                "source_watch": {
                    "binding": "current",
                    "last_check": {
                        "status": "not_modified",
                        "checked_at": "2026-08-16T07:00:00+00:00",
                    },
                },
                "read_only": True,
                "persistence_performed": False,
                "activation_performed": False,
            }

    def build_tariff_diagnostics(options, *, day):
        calls.append(("diagnostics", dict(options), day))
        if diagnostics_mode == "missing":
            raise LookupError("confirmed all-in tariff not found")
        if diagnostics_mode == "invalid":
            raise ValueError("confirmed tariff diagnostics are inconsistent")
        if diagnostics_mode == "error":
            raise RuntimeError("unexpected diagnostics failure")
        return Snapshot(day)

    diagnostics.build_tariff_diagnostics = build_tariff_diagnostics
    sys.modules[diagnostics.__name__] = diagnostics

    schemas = []
    registered = []
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

    class ActiveConnection:
        pass

    def websocket_command(schema):
        schemas.append(schema)

        def decorator(func):
            return func

        return decorator

    websocket_api.ActiveConnection = ActiveConnection
    websocket_api.websocket_command = websocket_command
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

    module = _load(
        "custom_components.frakon_energy.tariff_diagnostics_ws_api",
        "custom_components/frakon_energy/tariff_diagnostics_ws_api.py",
    )
    return module, registered, schemas, calls


class ConfigEntries:
    def __init__(self, entry):
        self.entry = entry

    def async_get_entry(self, entry_id):
        if self.entry is not None and self.entry.entry_id == entry_id:
            return self.entry
        return None


class Hass:
    def __init__(self, entry):
        self.data = {"frakon_energy": {}}
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


def _entry(*, domain="frakon_energy"):
    return types.SimpleNamespace(
        entry_id="entry-1",
        domain=domain,
        options={"confirmed_tariff_fixture": True},
    )


def _message(**overrides):
    message = {
        "id": 1,
        "type": "frakon_energy/tariff/diagnostics",
        "entry_id": "entry-1",
        "day": "2026-08-14",
    }
    message.update(overrides)
    return message


def test_registration_is_idempotent_and_schema_exposes_no_diagnostic_authority_input() -> None:
    module, registered, schemas, _calls = load_module()
    hass = Hass(_entry())

    module.async_register_tariff_diagnostics_websocket(hass)
    module.async_register_tariff_diagnostics_websocket(hass)

    assert len(registered) == 1
    assert set(schemas[0]) == {"type", "entry_id", "day"}
    for forbidden in (
        "source_url",
        "checksum",
        "document_date",
        "last_check",
        "parser_status",
        "authority_method",
        "all_in_tariff_fingerprint",
        "watch_fingerprint",
    ):
        assert forbidden not in schemas[0]


def test_success_returns_read_only_server_authoritative_diagnostics() -> None:
    module, registered, _schemas, calls = load_module()
    hass = Hass(_entry())
    connection = Connection()
    module.async_register_tariff_diagnostics_websocket(hass)

    asyncio.run(registered[0](hass, connection, _message()))

    assert connection.admin_calls == 1
    assert connection.errors == []
    assert calls == [
        ("diagnostics", {"confirmed_tariff_fixture": True}, date(2026, 8, 14))
    ]
    payload = connection.results[0][1]
    assert payload["entry_id"] == "entry-1"
    assert payload["day"] == "2026-08-14"
    assert payload["supplier_source"]["source_url"].startswith("https://")
    assert len(payload["supplier_source"]["checksum"]) == 64
    assert payload["source_watch"]["last_check"]["status"] == "not_modified"
    assert payload["parser"]["status"] == "verified_parser_active"
    assert payload["read_only"] is True
    assert payload["persistence_performed"] is False
    assert payload["activation_performed"] is False


def test_invalid_day_fails_before_diagnostics_lookup() -> None:
    module, registered, _schemas, calls = load_module()
    hass = Hass(_entry())
    connection = Connection()
    module.async_register_tariff_diagnostics_websocket(hass)

    asyncio.run(registered[0](hass, connection, _message(day="16.08.2026")))

    assert connection.errors[0][1] == "invalid_tariff_diagnostics_request"
    assert calls == []


def test_missing_invalid_and_unexpected_diagnostics_fail_closed() -> None:
    for mode, expected_code in (
        ("missing", "tariff_diagnostics_unavailable"),
        ("invalid", "invalid_tariff_diagnostics_request"),
        ("error", "tariff_diagnostics_failed"),
    ):
        module, registered, _schemas, _calls = load_module(diagnostics_mode=mode)
        hass = Hass(_entry())
        connection = Connection()
        module.async_register_tariff_diagnostics_websocket(hass)
        asyncio.run(registered[0](hass, connection, _message()))
        assert connection.errors[0][1] == expected_code
        assert connection.results == []


def test_wrong_domain_entry_fails_before_diagnostics_lookup() -> None:
    module, registered, _schemas, calls = load_module()
    hass = Hass(_entry(domain="other"))
    connection = Connection()
    module.async_register_tariff_diagnostics_websocket(hass)

    asyncio.run(registered[0](hass, connection, _message()))

    assert connection.errors[0][1] == "entry_not_found"
    assert calls == []
