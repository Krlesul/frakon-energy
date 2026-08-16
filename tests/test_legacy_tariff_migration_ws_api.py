import asyncio
from datetime import datetime, timezone
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
        "custom_components.frakon_energy.legacy_tariff_history",
        "custom_components.frakon_energy.legacy_tariff_migration_ws_api",
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
    history = _load(
        "custom_components.frakon_energy.legacy_tariff_history",
        "custom_components/frakon_energy/legacy_tariff_history.py",
    )

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

    util = types.ModuleType("homeassistant.util")
    util.__path__ = []
    sys.modules[util.__name__] = util
    dt = types.ModuleType("homeassistant.util.dt")
    dt.now = lambda: datetime(2026, 8, 16, 9, 0, tzinfo=timezone.utc)
    sys.modules[dt.__name__] = dt
    util.dt = dt

    module = _load(
        "custom_components.frakon_energy.legacy_tariff_migration_ws_api",
        "custom_components/frakon_energy/legacy_tariff_migration_ws_api.py",
    )
    return module, history, registered, schemas


class ConfigEntries:
    def __init__(self, entry):
        self.entry = entry
        self.updates = []

    def async_get_entry(self, entry_id):
        if self.entry is not None and self.entry.entry_id == entry_id:
            return self.entry
        return None

    def async_update_entry(self, entry, *, options):
        self.updates.append((entry, options))
        entry.options = options


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


def _options():
    return {
        "price_vt_czk_kwh": "7.52",
        "price_nt_czk_kwh": "4.67",
        "fixed_monthly_czk": "315.40",
    }


def _entry(*, options=None, domain="frakon_energy"):
    return types.SimpleNamespace(
        entry_id="entry-1",
        domain=domain,
        options=_options() if options is None else dict(options),
    )


def _propose(**overrides):
    message = {
        "id": 1,
        "type": "frakon_energy/tariff/legacy/propose",
        "entry_id": "entry-1",
        "valid_from": "2025-02-01",
        "valid_to": "2025-12-31",
    }
    message.update(overrides)
    return message


def _confirm(fingerprint):
    return {
        "id": 2,
        "type": "frakon_energy/tariff/legacy/confirm",
        "entry_id": "entry-1",
        "snapshot_fingerprint": fingerprint,
    }


def test_registration_is_idempotent_and_schemas_expose_no_price_or_authority_input() -> None:
    module, _history, registered, schemas = load_module()
    hass = Hass(_entry())

    module.async_register_legacy_tariff_migration_websocket(hass)
    module.async_register_legacy_tariff_migration_websocket(hass)

    assert len(registered) == 2
    assert set(schemas[0]) == {"type", "entry_id", "valid_from", "valid_to"}
    assert set(schemas[1]) == {"type", "entry_id", "snapshot_fingerprint"}
    for forbidden in (
        "price_vt_czk_kwh",
        "price_nt_czk_kwh",
        "fixed_monthly_czk",
        "authority_method",
        "component_breakdown",
        "source_url",
        "checksum",
        "supplier",
        "product_name",
    ):
        assert forbidden not in schemas[0]
        assert forbidden not in schemas[1]


def test_propose_copies_only_server_side_legacy_prices_and_never_activates() -> None:
    module, history, registered, _schemas = load_module()
    hass = Hass(_entry())
    connection = Connection()
    module.async_register_legacy_tariff_migration_websocket(hass)
    message = _propose()
    # Direct invocation bypasses voluptuous, so include hostile extras to prove the
    # handler still never reads client price or authority fields.
    message.update(
        {
            "price_vt_czk_kwh": "0.01",
            "price_nt_czk_kwh": "0.01",
            "fixed_monthly_czk": "0",
            "authority_method": "verified_parser",
        }
    )

    asyncio.run(registered[0](hass, connection, message))

    assert connection.admin_calls == 1
    assert connection.errors == []
    assert len(hass.config_entries.updates) == 1
    payload = connection.results[0][1]
    assert payload["high_rate_czk_per_kwh"] == "7.52"
    assert payload["low_rate_czk_per_kwh"] == "4.67"
    assert payload["fixed_monthly_czk"] == "315.40"
    assert payload["authority_method"] == "legacy_manual_import"
    assert payload["component_breakdown_available"] is False
    assert payload["official_provenance_available"] is False
    assert payload["historical_only"] is True
    assert payload["confirmed"] is False
    assert payload["persistence_performed"] is True
    assert payload["confirmation_performed"] is False
    assert payload["live_pricing_changed"] is False
    assert payload["activation_performed"] is False
    stored = history.legacy_tariff_history_from_options(hass.config_entries.entry.options)
    assert len(stored) == 1
    assert stored[0].confirmed is False


def test_propose_is_idempotent_and_never_rewrites_identical_snapshot() -> None:
    module, _history, registered, _schemas = load_module()
    hass = Hass(_entry())
    first = Connection()
    module.async_register_legacy_tariff_migration_websocket(hass)
    asyncio.run(registered[0](hass, first, _propose()))
    assert len(hass.config_entries.updates) == 1

    second = Connection()
    asyncio.run(registered[0](hass, second, _propose()))
    assert len(hass.config_entries.updates) == 1
    assert second.results[0][1]["persistence_performed"] is False
    assert second.results[0][1]["confirmed"] is False


def test_propose_rejects_current_or_future_window_before_persistence() -> None:
    module, _history, registered, _schemas = load_module()
    for valid_to in ("2026-08-16", "2026-08-17"):
        hass = Hass(_entry())
        connection = Connection()
        module.async_register_legacy_tariff_migration_websocket(hass)
        asyncio.run(
            registered[0](
                hass,
                connection,
                _propose(valid_from="2026-01-01", valid_to=valid_to),
            )
        )
        assert connection.errors[0][1] == "invalid_legacy_tariff_migration"
        assert hass.config_entries.updates == []


def test_missing_or_partial_server_legacy_prices_fail_closed() -> None:
    module, _history, registered, _schemas = load_module()
    for options, expected_code in (
        ({}, "legacy_tariff_prices_unavailable"),
        ({"price_vt_czk_kwh": "7.52"}, "invalid_legacy_tariff_migration"),
    ):
        hass = Hass(_entry(options=options))
        connection = Connection()
        module.async_register_legacy_tariff_migration_websocket(hass)
        asyncio.run(registered[0](hass, connection, _propose()))
        assert connection.errors[0][1] == expected_code
        assert hass.config_entries.updates == []


def test_confirm_accepts_only_stored_fingerprint_and_is_idempotent() -> None:
    module, history, registered, _schemas = load_module()
    hass = Hass(_entry())
    module.async_register_legacy_tariff_migration_websocket(hass)
    proposed = Connection()
    asyncio.run(registered[0](hass, proposed, _propose()))
    fingerprint = proposed.results[0][1]["fingerprint"]

    confirmed = Connection()
    asyncio.run(registered[1](hass, confirmed, _confirm(fingerprint)))
    assert confirmed.errors == []
    assert len(hass.config_entries.updates) == 2
    payload = confirmed.results[0][1]
    assert payload["confirmed"] is True
    assert payload["confirmation_performed"] is True
    assert payload["persistence_performed"] is True
    assert payload["activation_performed"] is False
    assert payload["live_pricing_changed"] is False
    stored = history.legacy_tariff_history_from_options(hass.config_entries.entry.options)
    assert stored[0].confirmed is True

    repeated = Connection()
    asyncio.run(registered[1](hass, repeated, _confirm(fingerprint)))
    assert len(hass.config_entries.updates) == 2
    assert repeated.results[0][1]["confirmed"] is True
    assert repeated.results[0][1]["confirmation_performed"] is False
    assert repeated.results[0][1]["persistence_performed"] is False


def test_confirmation_rejects_unknown_or_overlapping_snapshot_without_partial_write() -> None:
    module, _history, registered, _schemas = load_module()
    hass = Hass(_entry())
    module.async_register_legacy_tariff_migration_websocket(hass)

    first = Connection()
    asyncio.run(registered[0](hass, first, _propose(valid_from="2025-01-01", valid_to="2025-06-30")))
    first_fp = first.results[0][1]["fingerprint"]
    first_confirm = Connection()
    asyncio.run(registered[1](hass, first_confirm, _confirm(first_fp)))
    assert first_confirm.errors == []

    second = Connection()
    asyncio.run(registered[0](hass, second, _propose(valid_from="2025-06-01", valid_to="2025-12-31")))
    second_fp = second.results[0][1]["fingerprint"]
    writes_before = len(hass.config_entries.updates)
    second_confirm = Connection()
    asyncio.run(registered[1](hass, second_confirm, _confirm(second_fp)))
    assert second_confirm.errors[0][1] == "legacy_tariff_confirmation_failed"
    assert len(hass.config_entries.updates) == writes_before

    missing = Connection()
    asyncio.run(registered[1](hass, missing, _confirm("0" * 64)))
    assert missing.errors[0][1] == "legacy_tariff_snapshot_not_found"
    assert len(hass.config_entries.updates) == writes_before


def test_wrong_domain_entry_fails_before_migration() -> None:
    module, _history, registered, _schemas = load_module()
    hass = Hass(_entry(domain="other"))
    connection = Connection()
    module.async_register_legacy_tariff_migration_websocket(hass)

    asyncio.run(registered[0](hass, connection, _propose()))

    assert connection.errors[0][1] == "entry_not_found"
    assert hass.config_entries.updates == []
