import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import types


FIXED_NOW = datetime(2026, 8, 14, 15, 30, tzinfo=timezone(timedelta(hours=2)))


def load_module(
    *,
    due_result=None,
    due_error=None,
    track_error=None,
    create_task_error=None,
):
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.const",
        "custom_components.frakon_energy.tariff_update_ha",
        "custom_components.frakon_energy.tariff_update_runtime",
        "homeassistant",
        "homeassistant.core",
        "homeassistant.config_entries",
        "homeassistant.helpers",
        "homeassistant.helpers.event",
        "homeassistant.util",
        "homeassistant.util.dt",
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

    update_ha = types.ModuleType("custom_components.frakon_energy.tariff_update_ha")
    due_calls = []

    async def async_check_active_tariff_source_if_due_ha(hass, entry, **kwargs):
        due_calls.append((hass, entry, kwargs))
        if due_error is not None:
            raise due_error
        return due_result

    update_ha.async_check_active_tariff_source_if_due_ha = (
        async_check_active_tariff_source_if_due_ha
    )
    sys.modules[update_ha.__name__] = update_ha

    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    sys.modules["homeassistant"] = homeassistant

    core = types.ModuleType("homeassistant.core")

    def callback(func):
        return func

    class HomeAssistant:
        def __init__(self):
            self.data = {}
            self.created_tasks = []

        def async_create_task(self, coro):
            if create_task_error is not None:
                coro.close()
                raise create_task_error
            task = asyncio.create_task(coro)
            self.created_tasks.append(task)
            return task

    core.HomeAssistant = HomeAssistant
    core.callback = callback
    sys.modules["homeassistant.core"] = core

    config_entries = types.ModuleType("homeassistant.config_entries")

    class ConfigEntry:
        def __init__(self, entry_id="entry-1"):
            self.entry_id = entry_id
            self.unload_callbacks = []

        def async_on_unload(self, callback_fn):
            self.unload_callbacks.append(callback_fn)
            return callback_fn

    config_entries.ConfigEntry = ConfigEntry
    sys.modules["homeassistant.config_entries"] = config_entries

    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    sys.modules["homeassistant.helpers"] = helpers

    event = types.ModuleType("homeassistant.helpers.event")
    interval_calls = []
    unsubscribe_calls = []

    def async_track_time_interval(hass, action, interval, *, name=None):
        interval_calls.append((hass, action, interval, name))
        if track_error is not None:
            raise track_error

        def unsubscribe():
            unsubscribe_calls.append(True)

        return unsubscribe

    event.async_track_time_interval = async_track_time_interval
    sys.modules["homeassistant.helpers.event"] = event

    util = types.ModuleType("homeassistant.util")
    util.__path__ = []
    sys.modules["homeassistant.util"] = util

    dt = types.ModuleType("homeassistant.util.dt")
    dt.now = lambda: FIXED_NOW
    sys.modules["homeassistant.util.dt"] = dt
    util.dt = dt

    spec = importlib.util.spec_from_file_location(
        "custom_components.frakon_energy.tariff_update_runtime",
        Path("custom_components/frakon_energy/tariff_update_runtime.py"),
    )
    runtime = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runtime
    spec.loader.exec_module(runtime)
    return (
        runtime,
        HomeAssistant,
        ConfigEntry,
        interval_calls,
        unsubscribe_calls,
        due_calls,
    )


def test_runtime_registers_probe_timer_and_runs_immediate_due_evaluation() -> None:
    async def scenario():
        (
            runtime_module,
            HomeAssistant,
            ConfigEntry,
            interval_calls,
            _unsubscribe_calls,
            due_calls,
        ) = load_module(due_result=None)
        hass = HomeAssistant()
        entry = ConfigEntry()

        runtime = await runtime_module.async_start_tariff_update_runtime(hass, entry)
        await asyncio.sleep(0)

        assert runtime.started is True
        assert runtime.probe_interval == timedelta(hours=6)
        assert runtime.last_probe_at == FIXED_NOW
        assert runtime.last_check_status == "not_due"
        assert runtime.last_error is None
        assert len(interval_calls) == 1
        assert interval_calls[0][0] is hass
        assert interval_calls[0][2] == timedelta(hours=6)
        assert "entry-1" in interval_calls[0][3]
        assert len(entry.unload_callbacks) == 1
        assert due_calls == [
            (
                hass,
                entry,
                {
                    "day": FIXED_NOW.date(),
                    "checked_at": FIXED_NOW,
                },
            )
        ]

        same = await runtime_module.async_start_tariff_update_runtime(hass, entry)
        assert same is runtime
        assert len(interval_calls) == 1
        assert len(entry.unload_callbacks) == 1

        await runtime_module.async_stop_tariff_update_runtime(hass, entry.entry_id)

    asyncio.run(scenario())


def test_runtime_interval_probe_uses_due_gate_without_overlap() -> None:
    async def scenario():
        run = types.SimpleNamespace(check=types.SimpleNamespace(status="change_detected"))
        (
            runtime_module,
            HomeAssistant,
            ConfigEntry,
            interval_calls,
            _unsubscribe_calls,
            due_calls,
        ) = load_module(due_result=run)
        hass = HomeAssistant()
        entry = ConfigEntry()
        runtime = await runtime_module.async_start_tariff_update_runtime(hass, entry)
        await asyncio.sleep(0)

        assert runtime.last_check_status == "change_detected"
        timer_action = interval_calls[0][1]
        timer_action(FIXED_NOW + timedelta(hours=6))
        await asyncio.sleep(0)
        assert len(due_calls) == 2

        await runtime_module.async_stop_tariff_update_runtime(hass, entry.entry_id)

    asyncio.run(scenario())


def test_missing_confirmed_tariff_is_normal_preconfiguration_state() -> None:
    async def scenario():
        (
            runtime_module,
            HomeAssistant,
            ConfigEntry,
            _interval_calls,
            _unsubscribe_calls,
            due_calls,
        ) = load_module(due_error=LookupError("no confirmed tariff"))
        hass = HomeAssistant()
        entry = ConfigEntry()

        runtime = await runtime_module.async_start_tariff_update_runtime(hass, entry)
        await asyncio.sleep(0)

        assert len(due_calls) == 1
        assert runtime.last_check_status is None
        assert runtime.last_error is None

        await runtime_module.async_stop_tariff_update_runtime(hass, entry.entry_id)

    asyncio.run(scenario())


def test_invalid_confirmed_state_stays_fail_closed_without_stopping_runtime() -> None:
    async def scenario():
        (
            runtime_module,
            HomeAssistant,
            ConfigEntry,
            _interval_calls,
            _unsubscribe_calls,
            _due_calls,
        ) = load_module(due_error=ValueError("confirmed tariff mismatch"))
        hass = HomeAssistant()
        entry = ConfigEntry()

        runtime = await runtime_module.async_start_tariff_update_runtime(hass, entry)
        await asyncio.sleep(0)

        assert runtime.started is True
        assert runtime.last_check_status == "error"
        assert runtime.last_error == "confirmed tariff mismatch"

        await runtime_module.async_stop_tariff_update_runtime(hass, entry.entry_id)

    asyncio.run(scenario())


def test_explicit_stop_unsubscribes_timer_and_removes_runtime() -> None:
    async def scenario():
        (
            runtime_module,
            HomeAssistant,
            ConfigEntry,
            _interval_calls,
            unsubscribe_calls,
            _due_calls,
        ) = load_module(due_result=None)
        hass = HomeAssistant()
        entry = ConfigEntry()

        runtime = await runtime_module.async_start_tariff_update_runtime(hass, entry)
        await asyncio.sleep(0)
        await runtime_module.async_stop_tariff_update_runtime(hass, entry.entry_id)

        assert runtime.started is False
        assert unsubscribe_calls == [True]
        registry = hass.data["frakon_energy"]["tariff_update_runtimes_by_entry"]
        assert entry.entry_id not in registry

    asyncio.run(scenario())


def test_config_entry_unload_callback_stops_and_forgets_runtime_synchronously() -> None:
    async def scenario():
        (
            runtime_module,
            HomeAssistant,
            ConfigEntry,
            _interval_calls,
            unsubscribe_calls,
            _due_calls,
        ) = load_module(due_result=None)
        hass = HomeAssistant()
        entry = ConfigEntry()

        runtime = await runtime_module.async_start_tariff_update_runtime(hass, entry)
        await asyncio.sleep(0)
        entry.unload_callbacks[0]()

        assert runtime.started is False
        assert unsubscribe_calls == [True]
        registry = hass.data["frakon_energy"]["tariff_update_runtimes_by_entry"]
        assert entry.entry_id not in registry

    asyncio.run(scenario())


def test_timer_registration_failure_rolls_back_runtime_registry() -> None:
    async def scenario():
        (
            runtime_module,
            HomeAssistant,
            ConfigEntry,
            interval_calls,
            unsubscribe_calls,
            due_calls,
        ) = load_module(track_error=RuntimeError("timer registration failed"))
        hass = HomeAssistant()
        entry = ConfigEntry()

        try:
            await runtime_module.async_start_tariff_update_runtime(hass, entry)
        except RuntimeError as err:
            assert str(err) == "timer registration failed"
        else:
            raise AssertionError("Timer registration failure must escape setup")

        registry = hass.data["frakon_energy"]["tariff_update_runtimes_by_entry"]
        assert entry.entry_id not in registry
        assert len(interval_calls) == 1
        assert unsubscribe_calls == []
        assert due_calls == []
        assert entry.unload_callbacks == []

    asyncio.run(scenario())


def test_immediate_task_failure_unsubscribes_partial_timer_and_rolls_back() -> None:
    async def scenario():
        (
            runtime_module,
            HomeAssistant,
            ConfigEntry,
            interval_calls,
            unsubscribe_calls,
            due_calls,
        ) = load_module(create_task_error=RuntimeError("task creation failed"))
        hass = HomeAssistant()
        entry = ConfigEntry()

        try:
            await runtime_module.async_start_tariff_update_runtime(hass, entry)
        except RuntimeError as err:
            assert str(err) == "task creation failed"
        else:
            raise AssertionError("Immediate task failure must escape setup")

        registry = hass.data["frakon_energy"]["tariff_update_runtimes_by_entry"]
        assert entry.entry_id not in registry
        assert len(interval_calls) == 1
        assert unsubscribe_calls == [True]
        assert due_calls == []
        assert entry.unload_callbacks == []

    asyncio.run(scenario())
