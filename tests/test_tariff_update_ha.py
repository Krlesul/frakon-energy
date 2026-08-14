import asyncio
from datetime import date, datetime, timezone
import importlib.util
from pathlib import Path
import sys
import types


def load_modules(*, updated_options):
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.tariff_http_transport",
        "custom_components.frakon_energy.tariff_update_orchestrator",
        "custom_components.frakon_energy.tariff_update_ha",
        "homeassistant",
        "homeassistant.core",
        "homeassistant.config_entries",
        "homeassistant.helpers",
        "homeassistant.helpers.aiohttp_client",
    )
    for name in names:
        sys.modules.pop(name, None)

    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    transport = types.ModuleType("custom_components.frakon_energy.tariff_http_transport")
    transport.DEFAULT_TARIFF_HTTP_TIMEOUT_SECONDS = 20.0
    sys.modules[transport.__name__] = transport

    orchestrator = types.ModuleType("custom_components.frakon_energy.tariff_update_orchestrator")
    calls = []

    class TariffUpdateCheckRun:
        def __init__(self, options):
            self.updated_options = options
            self.activation_performed = False
            self.parser_authorized = False

    async def async_check_active_tariff_source(options, **kwargs):
        calls.append((options, kwargs))
        return TariffUpdateCheckRun(updated_options)

    orchestrator.TariffUpdateCheckRun = TariffUpdateCheckRun
    orchestrator.async_check_active_tariff_source = async_check_active_tariff_source
    sys.modules[orchestrator.__name__] = orchestrator

    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    sys.modules["homeassistant"] = homeassistant

    core = types.ModuleType("homeassistant.core")

    class ConfigEntriesManager:
        def __init__(self):
            self.update_calls = []

        def async_update_entry(self, entry, **kwargs):
            self.update_calls.append((entry, kwargs))

    class HomeAssistant:
        def __init__(self):
            self.config_entries = ConfigEntriesManager()

    core.HomeAssistant = HomeAssistant
    sys.modules["homeassistant.core"] = core

    config_entries = types.ModuleType("homeassistant.config_entries")

    class ConfigEntry:
        def __init__(self, options):
            self.options = options

    config_entries.ConfigEntry = ConfigEntry
    sys.modules["homeassistant.config_entries"] = config_entries

    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    sys.modules["homeassistant.helpers"] = helpers

    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    shared_session = object()
    session_calls = []

    def async_get_clientsession(hass):
        session_calls.append(hass)
        return shared_session

    aiohttp_client.async_get_clientsession = async_get_clientsession
    sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client

    spec = importlib.util.spec_from_file_location(
        "custom_components.frakon_energy.tariff_update_ha",
        Path("custom_components/frakon_energy/tariff_update_ha.py"),
    )
    adapter = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = adapter
    spec.loader.exec_module(adapter)
    return (
        adapter,
        HomeAssistant,
        ConfigEntry,
        shared_session,
        session_calls,
        calls,
    )


def test_ha_update_adapter_uses_shared_session_and_persists_changed_options_once() -> None:
    new_options = {
        "existing": True,
        "tariff_source_watches": [{"schema_version": 1}],
    }
    (
        adapter,
        HomeAssistant,
        ConfigEntry,
        shared_session,
        session_calls,
        orchestrator_calls,
    ) = load_modules(updated_options=new_options)
    hass = HomeAssistant()
    entry = ConfigEntry({"existing": True})
    day = date(2026, 8, 14)
    checked_at = datetime(2026, 8, 14, 8, 45, tzinfo=timezone.utc)

    run = asyncio.run(
        adapter.async_check_active_tariff_source_ha(
            hass,
            entry,
            day=day,
            checked_at=checked_at,
            timeout_seconds=8.5,
        )
    )

    assert run.updated_options == new_options
    assert run.activation_performed is False
    assert session_calls == [hass]
    assert orchestrator_calls == [
        (
            entry.options,
            {
                "day": day,
                "session": shared_session,
                "checked_at": checked_at,
                "timeout_seconds": 8.5,
            },
        )
    ]
    assert hass.config_entries.update_calls == [
        (entry, {"options": new_options})
    ]


def test_ha_update_adapter_does_not_write_when_options_are_unchanged() -> None:
    existing = {
        "existing": True,
        "tariff_source_watches": [{"schema_version": 1}],
    }
    (
        adapter,
        HomeAssistant,
        ConfigEntry,
        shared_session,
        session_calls,
        orchestrator_calls,
    ) = load_modules(updated_options=dict(existing))
    hass = HomeAssistant()
    entry = ConfigEntry(existing)
    day = date(2026, 8, 14)
    checked_at = datetime(2026, 8, 14, 8, 50, tzinfo=timezone.utc)

    run = asyncio.run(
        adapter.async_check_active_tariff_source_ha(
            hass,
            entry,
            day=day,
            checked_at=checked_at,
        )
    )

    assert run.updated_options == existing
    assert session_calls == [hass]
    assert orchestrator_calls[0][1]["session"] is shared_session
    assert orchestrator_calls[0][1]["timeout_seconds"] == 20.0
    assert hass.config_entries.update_calls == []


def test_ha_update_adapter_rejects_naive_checked_at_before_network_or_write() -> None:
    (
        adapter,
        HomeAssistant,
        ConfigEntry,
        _shared_session,
        session_calls,
        orchestrator_calls,
    ) = load_modules(updated_options={})
    hass = HomeAssistant()
    entry = ConfigEntry({})

    try:
        asyncio.run(
            adapter.async_check_active_tariff_source_ha(
                hass,
                entry,
                day=date(2026, 8, 14),
                checked_at=datetime(2026, 8, 14, 8, 55),
            )
        )
    except ValueError as err:
        assert "timezone-aware" in str(err)
    else:
        raise AssertionError("Naive update timestamp must fail closed")

    assert session_calls == []
    assert orchestrator_calls == []
    assert hass.config_entries.update_calls == []
