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


def load_module(*, pricing_mode="success", history_available=True):
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.const",
        "custom_components.frakon_energy.daily_all_in_costs",
        "custom_components.frakon_energy.daily_all_in_costs_ws_api",
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

    pricing = types.ModuleType("custom_components.frakon_energy.daily_all_in_costs")

    @dataclass(frozen=True)
    class Priced:
        day: date

        def as_dict(self):
            return {
                "day": self.day.isoformat(),
                "variable_cost_czk": "12.34",
                "fixed_monthly_excluded": True,
            }

    def price_confirmed_daily_consumption(options, records):
        materialized = tuple(records)
        calls.append(("price", dict(options), materialized))
        if pricing_mode == "missing":
            raise LookupError("all-in tariff authority not found")
        if pricing_mode == "invalid":
            raise ValueError("duplicate calendar days")
        return tuple(Priced(item.day) for item in materialized)

    def summarize_daily_all_in_costs(records):
        materialized = tuple(records)
        calls.append(("summary", materialized))
        return {
            "days": len(materialized),
            "variable_cost_czk": "12.34" if materialized else "0.00",
            "fixed_monthly_excluded": True,
        }

    pricing.price_confirmed_daily_consumption = price_confirmed_daily_consumption
    pricing.summarize_daily_all_in_costs = summarize_daily_all_in_costs
    sys.modules[pricing.__name__] = pricing

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

    module = _load(
        "custom_components.frakon_energy.daily_all_in_costs_ws_api",
        "custom_components/frakon_energy/daily_all_in_costs_ws_api.py",
    )
    return module, registered, schemas, calls, history_available


@dataclass(frozen=True)
class Daily:
    day: date
    high_rate_kwh: int = 1
    low_rate_kwh: int = 2


class History:
    def daily_consumption(self):
        return (
            Daily(date(2026, 8, 13)),
            Daily(date(2026, 8, 14)),
            Daily(date(2026, 8, 15)),
        )


class ConfigEntries:
    def __init__(self, entry):
        self.entry = entry

    def async_get_entry(self, entry_id):
        if self.entry is not None and self.entry.entry_id == entry_id:
            return self.entry
        return None


class Hass:
    def __init__(self, entry, *, history_available=True):
        coordinator = types.SimpleNamespace(history=History()) if history_available else types.SimpleNamespace()
        self.data = {"frakon_energy": {entry.entry_id: coordinator}}
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
        "type": "frakon_energy/tariff/daily_costs",
        "entry_id": "entry-1",
        "start_day": "2026-08-14",
        "end_day": "2026-08-15",
    }
    message.update(overrides)
    return message


def test_registration_is_idempotent_and_schema_exposes_no_price_authority() -> None:
    module, registered, schemas, _calls, _history = load_module()
    hass = Hass(_entry())

    module.async_register_daily_all_in_costs_websocket(hass)
    module.async_register_daily_all_in_costs_websocket(hass)

    assert len(registered) == 1
    assert set(schemas[0]) == {"type", "entry_id", "start_day", "end_day"}
    for forbidden in (
        "price",
        "tariff",
        "fingerprint",
        "authority_method",
        "source_url",
        "fixed_monthly_czk",
    ):
        assert forbidden not in schemas[0]


def test_success_filters_real_history_and_returns_variable_only_confirmed_costs() -> None:
    module, registered, _schemas, calls, _history = load_module()
    hass = Hass(_entry())
    connection = Connection()
    module.async_register_daily_all_in_costs_websocket(hass)

    asyncio.run(registered[0](hass, connection, _message()))

    assert connection.admin_calls == 1
    assert connection.errors == []
    priced_call = next(item for item in calls if item[0] == "price")
    assert [item.day for item in priced_call[2]] == [date(2026, 8, 14), date(2026, 8, 15)]
    payload = connection.results[0][1]
    assert payload["entry_id"] == "entry-1"
    assert payload["price_source"] == "confirmed_all_in"
    assert payload["fixed_monthly_excluded"] is True
    assert payload["read_only"] is True
    assert payload["persistence_performed"] is False
    assert payload["activation_performed"] is False
    assert [item["day"] for item in payload["records"]] == ["2026-08-14", "2026-08-15"]
    assert payload["summary"]["fixed_monthly_excluded"] is True


def test_invalid_range_fails_before_history_pricing() -> None:
    module, registered, _schemas, calls, _history = load_module()
    hass = Hass(_entry())
    connection = Connection()
    module.async_register_daily_all_in_costs_websocket(hass)

    asyncio.run(
        registered[0](
            hass,
            connection,
            _message(start_day="2026-08-15", end_day="2026-08-14"),
        )
    )
    assert connection.errors[0][1] == "invalid_daily_cost_request"
    assert not any(item[0] == "price" for item in calls)

    connection = Connection()
    asyncio.run(
        registered[0](
            hass,
            connection,
            _message(start_day="2025-01-01", end_day="2026-08-15"),
        )
    )
    assert connection.errors[0][1] == "invalid_daily_cost_request"
    assert not any(item[0] == "price" for item in calls)


def test_missing_history_and_missing_tariff_fail_closed() -> None:
    module, registered, _schemas, _calls, _history = load_module()
    hass = Hass(_entry(), history_available=False)
    connection = Connection()
    module.async_register_daily_all_in_costs_websocket(hass)
    asyncio.run(registered[0](hass, connection, _message()))
    assert connection.errors[0][1] == "daily_cost_tariff_unavailable"
    assert connection.results == []

    module, registered, _schemas, _calls, _history = load_module(pricing_mode="missing")
    hass = Hass(_entry())
    connection = Connection()
    module.async_register_daily_all_in_costs_websocket(hass)
    asyncio.run(registered[0](hass, connection, _message()))
    assert connection.errors[0][1] == "daily_cost_tariff_unavailable"
    assert connection.results == []


def test_wrong_domain_entry_fails_without_touching_history() -> None:
    module, registered, _schemas, calls, _history = load_module()
    hass = Hass(_entry(domain="other"))
    connection = Connection()
    module.async_register_daily_all_in_costs_websocket(hass)

    asyncio.run(registered[0](hass, connection, _message()))

    assert connection.errors[0][1] == "entry_not_found"
    assert not any(item[0] == "price" for item in calls)
