import asyncio
from datetime import date, datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import types


def load_modules(*, updated_options, due=True):
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.const",
        "custom_components.frakon_energy.tariff_http_transport",
        "custom_components.frakon_energy.tariff_update_cadence",
        "custom_components.frakon_energy.tariff_update_notifications",
        "custom_components.frakon_energy.tariff_update_orchestrator",
        "custom_components.frakon_energy.tariff_update_ha",
        "homeassistant",
        "homeassistant.components",
        "homeassistant.components.persistent_notification",
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

    const = types.ModuleType("custom_components.frakon_energy.const")
    const.DOMAIN = "frakon_energy"
    sys.modules[const.__name__] = const

    transport = types.ModuleType("custom_components.frakon_energy.tariff_http_transport")
    transport.DEFAULT_TARIFF_HTTP_TIMEOUT_SECONDS = 20.0
    sys.modules[transport.__name__] = transport

    cadence = types.ModuleType("custom_components.frakon_energy.tariff_update_cadence")
    cadence.DEFAULT_TARIFF_UPDATE_INTERVAL = timedelta(days=7)
    cadence_calls = []

    def active_tariff_check_cadence(options, **kwargs):
        cadence_calls.append((options, kwargs))
        return types.SimpleNamespace(due=due)

    cadence.active_tariff_check_cadence = active_tariff_check_cadence
    sys.modules[cadence.__name__] = cadence

    notifications = types.ModuleType(
        "custom_components.frakon_energy.tariff_update_notifications"
    )
    notifications.pending_calls = []
    notifications.decision_calls = []
    notifications.result = None

    def pending_tariff_hashes(options):
        notifications.pending_calls.append(options)
        return dict(options.get("_pending_before", {}))

    def notification_for_new_pending_tariff(run, *, pending_before):
        notifications.decision_calls.append((run, pending_before))
        return notifications.result

    notifications.pending_tariff_hashes = pending_tariff_hashes
    notifications.notification_for_new_pending_tariff = notification_for_new_pending_tariff
    sys.modules[notifications.__name__] = notifications

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

    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    sys.modules["homeassistant.components"] = components

    persistent_notification = types.ModuleType(
        "homeassistant.components.persistent_notification"
    )
    persistent_notification.calls = []

    def async_create(hass, message, title=None, notification_id=None):
        persistent_notification.calls.append(
            {
                "hass": hass,
                "message": message,
                "title": title,
                "notification_id": notification_id,
            }
        )

    persistent_notification.async_create = async_create
    components.persistent_notification = persistent_notification
    sys.modules[persistent_notification.__name__] = persistent_notification

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
        def __init__(self, options, entry_id="entry-1"):
            self.options = options
            self.entry_id = entry_id

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
    adapter._test_notifications = notifications
    adapter._test_persistent_notification = persistent_notification
    return (
        adapter,
        HomeAssistant,
        ConfigEntry,
        shared_session,
        session_calls,
        calls,
        cadence_calls,
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
        _cadence_calls,
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
        _cadence_calls,
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
        _cadence_calls,
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


def test_due_adapter_skips_network_write_and_notification_state_before_boundary() -> None:
    (
        adapter,
        HomeAssistant,
        ConfigEntry,
        _shared_session,
        session_calls,
        orchestrator_calls,
        cadence_calls,
    ) = load_modules(updated_options={}, due=False)
    hass = HomeAssistant()
    entry = ConfigEntry({"existing": True})
    day = date(2026, 8, 14)
    checked_at = datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc)

    result = asyncio.run(
        adapter.async_check_active_tariff_source_if_due_ha(
            hass,
            entry,
            day=day,
            checked_at=checked_at,
        )
    )

    assert result is None
    assert cadence_calls == [
        (
            entry.options,
            {
                "day": day,
                "checked_at": checked_at,
                "interval": timedelta(days=7),
            },
        )
    ]
    assert adapter._test_notifications.pending_calls == []
    assert adapter._test_notifications.decision_calls == []
    assert adapter._test_persistent_notification.calls == []
    assert session_calls == []
    assert orchestrator_calls == []
    assert hass.config_entries.update_calls == []


def test_due_adapter_runs_existing_safe_check_when_cadence_is_due() -> None:
    new_options = {"tariff_source_watches": [{"schema_version": 1}]}
    (
        adapter,
        HomeAssistant,
        ConfigEntry,
        shared_session,
        session_calls,
        orchestrator_calls,
        cadence_calls,
    ) = load_modules(updated_options=new_options, due=True)
    hass = HomeAssistant()
    entry = ConfigEntry({})
    day = date(2026, 8, 14)
    checked_at = datetime(2026, 8, 14, 9, 5, tzinfo=timezone.utc)

    result = asyncio.run(
        adapter.async_check_active_tariff_source_if_due_ha(
            hass,
            entry,
            day=day,
            checked_at=checked_at,
            interval=timedelta(days=3),
            timeout_seconds=7.0,
        )
    )

    assert result is not None
    assert cadence_calls == [
        (
            entry.options,
            {
                "day": day,
                "checked_at": checked_at,
                "interval": timedelta(days=3),
            },
        )
    ]
    assert adapter._test_notifications.pending_calls == [{}]
    assert adapter._test_notifications.decision_calls == [(result, {})]
    assert session_calls == [hass]
    assert orchestrator_calls == [
        (
            entry.options,
            {
                "day": day,
                "session": shared_session,
                "checked_at": checked_at,
                "timeout_seconds": 7.0,
            },
        )
    ]
    assert hass.config_entries.update_calls == [
        (entry, {"options": new_options})
    ]
    assert adapter._test_persistent_notification.calls == []


def test_due_adapter_creates_stable_notification_for_new_pending_tariff() -> None:
    (
        adapter,
        HomeAssistant,
        ConfigEntry,
        _shared_session,
        _session_calls,
        _orchestrator_calls,
        _cadence_calls,
    ) = load_modules(updated_options={"updated": True}, due=True)
    hass = HomeAssistant()
    entry = ConfigEntry(
        {"_pending_before": {"f" * 64: None}},
        entry_id="tariff-entry",
    )
    adapter._test_notifications.result = types.SimpleNamespace(
        title="FRAKON Energy: tariff update available",
        message="New tariff requires review",
    )

    run = asyncio.run(
        adapter.async_check_active_tariff_source_if_due_ha(
            hass,
            entry,
            day=date(2026, 8, 14),
            checked_at=datetime(2026, 8, 14, 9, 10, tzinfo=timezone.utc),
        )
    )

    assert run is not None
    assert adapter._test_notifications.pending_calls == [
        {"_pending_before": {"f" * 64: None}}
    ]
    assert adapter._test_notifications.decision_calls == [
        (run, {"f" * 64: None})
    ]
    assert adapter._test_persistent_notification.calls == [
        {
            "hass": hass,
            "message": "New tariff requires review",
            "title": "FRAKON Energy: tariff update available",
            "notification_id": "frakon_energy_tariff_update_tariff-entry",
        }
    ]