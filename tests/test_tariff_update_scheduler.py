import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import types


def load_module():
    names = (
        "homeassistant",
        "homeassistant.components",
        "homeassistant.components.persistent_notification",
        "homeassistant.config_entries",
        "homeassistant.core",
        "homeassistant.helpers",
        "homeassistant.helpers.event",
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.const",
        "custom_components.frakon_energy.tariff_source_watch",
        "custom_components.frakon_energy.tariff_source_watch_store",
        "custom_components.frakon_energy.tariff_update_ha",
        "custom_components.frakon_energy.tariff_update_orchestrator",
        "custom_components.frakon_energy.tariff_update_scheduler",
    )
    for name in names:
        sys.modules.pop(name, None)

    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
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

    config_entries = types.ModuleType("homeassistant.config_entries")

    class ConfigEntry:
        def __init__(self, *, entry_id="entry-1", options=None):
            self.entry_id = entry_id
            self.options = options if options is not None else {}
            self.unload_callbacks = []

        def async_on_unload(self, callback):
            self.unload_callbacks.append(callback)

    config_entries.ConfigEntry = ConfigEntry

    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:
        pass

    core.HomeAssistant = HomeAssistant

    helpers = types.ModuleType("homeassistant.helpers")
    event = types.ModuleType("homeassistant.helpers.event")
    event.calls = []

    def async_track_time_interval(hass, action, interval, *, name=None):
        def unsubscribe():
            return None

        event.calls.append(
            {
                "hass": hass,
                "action": action,
                "interval": interval,
                "name": name,
                "unsubscribe": unsubscribe,
            }
        )
        return unsubscribe

    event.async_track_time_interval = async_track_time_interval
    helpers.event = event

    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.components"] = components
    sys.modules[
        "homeassistant.components.persistent_notification"
    ] = persistent_notification
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.event"] = event

    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    const = types.ModuleType("custom_components.frakon_energy.const")
    const.DOMAIN = "frakon_energy"
    sys.modules[const.__name__] = const

    source_watch = types.ModuleType(
        "custom_components.frakon_energy.tariff_source_watch"
    )
    source_watch.STATUS_CHANGE_DETECTED = "change_detected"
    source_watch.tariff_source_watch_fingerprint = lambda watch: watch.fingerprint
    sys.modules[source_watch.__name__] = source_watch

    store = types.ModuleType(
        "custom_components.frakon_energy.tariff_source_watch_store"
    )
    store.tariff_source_watch_records_from_options = (
        lambda options: tuple(options.get("records", ()))
    )
    sys.modules[store.__name__] = store

    orchestrator = types.ModuleType(
        "custom_components.frakon_energy.tariff_update_orchestrator"
    )

    class TariffUpdateCheckRun:
        def __init__(self, *, check, watch, activation_performed=False):
            self.check = check
            self.prepared = types.SimpleNamespace(
                record=types.SimpleNamespace(watch=watch)
            )
            self.activation_performed = activation_performed

    orchestrator.TariffUpdateCheckRun = TariffUpdateCheckRun
    sys.modules[orchestrator.__name__] = orchestrator

    update_ha = types.ModuleType("custom_components.frakon_energy.tariff_update_ha")
    update_ha.impl = None
    update_ha.calls = []

    async def async_check_active_tariff_source_if_due_ha(*args, **kwargs):
        update_ha.calls.append((args, kwargs))
        if update_ha.impl is None:
            raise AssertionError("test did not configure tariff update implementation")
        return await update_ha.impl(*args, **kwargs)

    update_ha.async_check_active_tariff_source_if_due_ha = (
        async_check_active_tariff_source_if_due_ha
    )
    sys.modules[update_ha.__name__] = update_ha

    spec = importlib.util.spec_from_file_location(
        "custom_components.frakon_energy.tariff_update_scheduler",
        Path("custom_components/frakon_energy/tariff_update_scheduler.py"),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, persistent_notification, event, config_entries, core, update_ha, orchestrator


def _run(orchestrator, *, status="change_detected", observed="b" * 64):
    watch = types.SimpleNamespace(
        fingerprint="f" * 64,
        product_name="Basic",
        source_name="ČEZ Prodej",
        document_name="Basic 2026",
        source_url="https://www.cez.cz/file/basic.pdf",
    )
    check = types.SimpleNamespace(
        status=status,
        observed_sha256=observed,
        requires_confirmation=status == "change_detected",
        watch_fingerprint=watch.fingerprint,
    )
    return orchestrator.TariffUpdateCheckRun(check=check, watch=watch)


def test_new_pending_hash_creates_one_notification_without_activation() -> None:
    module, notifications, _, config_entries, core, update_ha, orchestrator = load_module()
    entry = config_entries.ConfigEntry(
        options={
            "records": [
                types.SimpleNamespace(
                    watch=types.SimpleNamespace(fingerprint="f" * 64),
                    pending_sha256=None,
                )
            ]
        }
    )
    hass = core.HomeAssistant()

    async def impl(*args, **kwargs):
        return _run(orchestrator)

    update_ha.impl = impl
    result = asyncio.run(
        module.async_run_scheduled_tariff_update(
            hass,
            entry,
            now=datetime(2026, 8, 14, 15, 0, tzinfo=timezone.utc),
        )
    )

    assert result.check_performed is True
    assert result.notification_created is True
    assert result.run.activation_performed is False
    assert len(notifications.calls) == 1
    notification = notifications.calls[0]
    assert notification["notification_id"] == "frakon_energy_tariff_update_entry-1"
    assert "Basic" in notification["message"]
    assert "has not changed" in notification["message"]
    assert "b" * 64 in notification["message"]


def test_same_pending_hash_does_not_notify_again() -> None:
    module, notifications, _, config_entries, core, update_ha, orchestrator = load_module()
    entry = config_entries.ConfigEntry(
        options={
            "records": [
                types.SimpleNamespace(
                    watch=types.SimpleNamespace(fingerprint="f" * 64),
                    pending_sha256="b" * 64,
                )
            ]
        }
    )

    async def impl(*args, **kwargs):
        return _run(orchestrator)

    update_ha.impl = impl
    result = asyncio.run(
        module.async_run_scheduled_tariff_update(
            core.HomeAssistant(),
            entry,
            now=datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc),
        )
    )

    assert result.check_performed is True
    assert result.notification_created is False
    assert notifications.calls == []


def test_cadence_skip_never_notifies_or_claims_check() -> None:
    module, notifications, _, config_entries, core, update_ha, _ = load_module()
    entry = config_entries.ConfigEntry(options={"records": []})

    async def impl(*args, **kwargs):
        return None

    update_ha.impl = impl
    result = asyncio.run(
        module.async_run_scheduled_tariff_update(
            core.HomeAssistant(),
            entry,
            now=datetime(2026, 8, 14, 15, 0, tzinfo=timezone.utc),
        )
    )

    assert result.check_performed is False
    assert result.run is None
    assert result.notification_created is False
    assert notifications.calls == []


def test_non_change_result_never_notifies() -> None:
    module, notifications, _, config_entries, core, update_ha, orchestrator = load_module()
    entry = config_entries.ConfigEntry(options={"records": []})

    async def impl(*args, **kwargs):
        return _run(orchestrator, status="not_modified", observed=None)

    update_ha.impl = impl
    result = asyncio.run(
        module.async_run_scheduled_tariff_update(
            core.HomeAssistant(),
            entry,
            now=datetime(2026, 8, 14, 15, 0, tzinfo=timezone.utc),
        )
    )

    assert result.check_performed is True
    assert result.notification_created is False
    assert notifications.calls == []


def test_scheduler_wakes_hourly_and_unregisters_on_entry_unload() -> None:
    module, notifications, event, config_entries, core, update_ha, _ = load_module()
    entry = config_entries.ConfigEntry()
    hass = core.HomeAssistant()

    async def impl(*args, **kwargs):
        return None

    update_ha.impl = impl
    module.async_start_tariff_update_scheduler(hass, entry)

    assert len(event.calls) == 1
    registration = event.calls[0]
    assert registration["interval"] == timedelta(hours=1)
    assert registration["name"] == "FRAKON Energy tariff source update wake"
    assert entry.unload_callbacks == [registration["unsubscribe"]]

    asyncio.run(
        registration["action"](
            datetime(2026, 8, 14, 16, 0, tzinfo=timezone.utc)
        )
    )
    assert len(update_ha.calls) == 1
    assert notifications.calls == []


def test_missing_confirmed_tariff_is_dormant_not_user_visible_error() -> None:
    module, notifications, event, config_entries, core, update_ha, _ = load_module()
    entry = config_entries.ConfigEntry()

    async def impl(*args, **kwargs):
        raise LookupError("no confirmed active tariff")

    update_ha.impl = impl
    module.async_start_tariff_update_scheduler(core.HomeAssistant(), entry)
    asyncio.run(
        event.calls[0]["action"](
            datetime(2026, 8, 14, 16, 0, tzinfo=timezone.utc)
        )
    )

    assert notifications.calls == []


def test_scheduled_run_requires_timezone_aware_now() -> None:
    module, _, _, config_entries, core, update_ha, _ = load_module()

    async def impl(*args, **kwargs):
        return None

    update_ha.impl = impl
    try:
        asyncio.run(
            module.async_run_scheduled_tariff_update(
                core.HomeAssistant(),
                config_entries.ConfigEntry(),
                now=datetime(2026, 8, 14, 15, 0),
            )
        )
    except ValueError as err:
        assert "timezone-aware" in str(err)
    else:
        raise AssertionError("Naive scheduler time must fail closed")
