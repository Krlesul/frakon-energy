import importlib.util
from pathlib import Path
import sys
import types


PENDING_A = "a" * 64
PENDING_B = "b" * 64


def load_module(*, final_pending):
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.const",
        "custom_components.frakon_energy.tariff_source_watch",
        "custom_components.frakon_energy.tariff_source_watch_store",
        "custom_components.frakon_energy.tariff_update_orchestrator",
        "custom_components.frakon_energy.tariff_update_notification",
        "homeassistant",
        "homeassistant.components",
        "homeassistant.components.persistent_notification",
        "homeassistant.config_entries",
        "homeassistant.core",
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

    source_watch = types.ModuleType("custom_components.frakon_energy.tariff_source_watch")
    source_watch.STATUS_CHANGE_DETECTED = "change_detected"
    sys.modules[source_watch.__name__] = source_watch

    store = types.ModuleType("custom_components.frakon_energy.tariff_source_watch_store")
    store_calls = []

    def tariff_source_watch_record_from_options(options, watch_fingerprint):
        store_calls.append((options, watch_fingerprint))
        return types.SimpleNamespace(pending_sha256=final_pending)

    store.tariff_source_watch_record_from_options = tariff_source_watch_record_from_options
    sys.modules[store.__name__] = store

    orchestrator = types.ModuleType(
        "custom_components.frakon_energy.tariff_update_orchestrator"
    )

    class TariffUpdateCheckRun:
        def __init__(
            self,
            *,
            prior_pending,
            status="change_detected",
            requires_confirmation=True,
            observed=PENDING_A,
            activation_performed=False,
        ):
            watch = types.SimpleNamespace(
                product_name="Elektřina Online PRO",
                document_name="cenik-elektrina.pdf",
                source_url="https://example.invalid/cenik-elektrina.pdf",
            )
            self.prepared = types.SimpleNamespace(
                record=types.SimpleNamespace(
                    pending_sha256=prior_pending,
                    watch=watch,
                )
            )
            self.check = types.SimpleNamespace(
                watch_fingerprint="watch-1",
                status=status,
                requires_confirmation=requires_confirmation,
                observed_sha256=observed,
            )
            self.updated_options = {"updated": True}
            self.activation_performed = activation_performed

    orchestrator.TariffUpdateCheckRun = TariffUpdateCheckRun
    sys.modules[orchestrator.__name__] = orchestrator

    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    sys.modules["homeassistant"] = homeassistant

    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    sys.modules["homeassistant.components"] = components

    persistent = types.ModuleType("homeassistant.components.persistent_notification")
    create_calls = []
    dismiss_calls = []

    def async_create(hass, message, title=None, notification_id=None):
        create_calls.append((hass, message, title, notification_id))

    def async_dismiss(hass, notification_id=None):
        dismiss_calls.append((hass, notification_id))

    persistent.async_create = async_create
    persistent.async_dismiss = async_dismiss
    sys.modules[persistent.__name__] = persistent
    components.persistent_notification = persistent

    config_entries = types.ModuleType("homeassistant.config_entries")

    class ConfigEntry:
        def __init__(self, entry_id="entry-1"):
            self.entry_id = entry_id

    config_entries.ConfigEntry = ConfigEntry
    sys.modules["homeassistant.config_entries"] = config_entries

    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:
        pass

    core.HomeAssistant = HomeAssistant
    sys.modules["homeassistant.core"] = core

    spec = importlib.util.spec_from_file_location(
        "custom_components.frakon_energy.tariff_update_notification",
        Path("custom_components/frakon_energy/tariff_update_notification.py"),
    )
    notification = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = notification
    spec.loader.exec_module(notification)
    return (
        notification,
        TariffUpdateCheckRun,
        HomeAssistant,
        ConfigEntry,
        create_calls,
        dismiss_calls,
        store_calls,
    )


def test_new_pending_hash_creates_exactly_one_non_activation_notification() -> None:
    (
        notification,
        TariffUpdateCheckRun,
        HomeAssistant,
        ConfigEntry,
        create_calls,
        dismiss_calls,
        store_calls,
    ) = load_module(final_pending=PENDING_A)
    hass = HomeAssistant()
    entry = ConfigEntry()
    run = TariffUpdateCheckRun(prior_pending=None)

    created = notification.async_sync_tariff_update_notification(hass, entry, run)

    assert created is True
    assert store_calls == [({"updated": True}, "watch-1")]
    assert dismiss_calls == []
    assert len(create_calls) == 1
    assert create_calls[0][0] is hass
    assert "currently confirmed tariff active" in create_calls[0][1]
    assert "No electricity price has been changed automatically" in create_calls[0][1]
    assert create_calls[0][2] == "FRAKON Energy: tariff document changed"
    assert create_calls[0][3] == "frakon_energy_tariff_update_entry-1"


def test_same_pending_hash_is_not_notified_twice() -> None:
    (
        notification,
        TariffUpdateCheckRun,
        HomeAssistant,
        ConfigEntry,
        create_calls,
        dismiss_calls,
        _store_calls,
    ) = load_module(final_pending=PENDING_A)
    hass = HomeAssistant()
    entry = ConfigEntry()
    run = TariffUpdateCheckRun(prior_pending=PENDING_A)

    assert notification.async_sync_tariff_update_notification(hass, entry, run) is False
    assert create_calls == []
    assert dismiss_calls == []


def test_non_change_result_preserves_existing_pending_notification_without_recreate() -> None:
    (
        notification,
        TariffUpdateCheckRun,
        HomeAssistant,
        ConfigEntry,
        create_calls,
        dismiss_calls,
        _store_calls,
    ) = load_module(final_pending=PENDING_A)
    hass = HomeAssistant()
    entry = ConfigEntry()
    run = TariffUpdateCheckRun(
        prior_pending=PENDING_A,
        status="not_modified",
        requires_confirmation=False,
        observed=None,
    )

    assert notification.async_sync_tariff_update_notification(hass, entry, run) is False
    assert create_calls == []
    assert dismiss_calls == []


def test_cleared_pending_state_dismisses_stale_notification() -> None:
    (
        notification,
        TariffUpdateCheckRun,
        HomeAssistant,
        ConfigEntry,
        create_calls,
        dismiss_calls,
        _store_calls,
    ) = load_module(final_pending=None)
    hass = HomeAssistant()
    entry = ConfigEntry()
    run = TariffUpdateCheckRun(
        prior_pending=PENDING_A,
        status="unchanged_hash",
        requires_confirmation=False,
        observed=PENDING_B,
    )

    assert notification.async_sync_tariff_update_notification(hass, entry, run) is False
    assert create_calls == []
    assert dismiss_calls == [(hass, "frakon_energy_tariff_update_entry-1")]


def test_notification_rejects_pending_hash_mismatch() -> None:
    (
        notification,
        TariffUpdateCheckRun,
        HomeAssistant,
        ConfigEntry,
        create_calls,
        dismiss_calls,
        _store_calls,
    ) = load_module(final_pending=PENDING_B)
    hass = HomeAssistant()
    entry = ConfigEntry()
    run = TariffUpdateCheckRun(prior_pending=None, observed=PENDING_A)

    try:
        notification.async_sync_tariff_update_notification(hass, entry, run)
    except ValueError as err:
        assert "durable pending hash" in str(err)
    else:
        raise AssertionError("Mismatched pending hash must fail closed")

    assert create_calls == []
    assert dismiss_calls == []


def test_notification_rejects_any_run_that_claims_activation() -> None:
    (
        notification,
        TariffUpdateCheckRun,
        HomeAssistant,
        ConfigEntry,
        create_calls,
        dismiss_calls,
        store_calls,
    ) = load_module(final_pending=PENDING_A)
    hass = HomeAssistant()
    entry = ConfigEntry()
    run = TariffUpdateCheckRun(prior_pending=None, activation_performed=True)

    try:
        notification.async_sync_tariff_update_notification(hass, entry, run)
    except ValueError as err:
        assert "cannot accompany activation" in str(err)
    else:
        raise AssertionError("Activation-bearing notification run must fail closed")

    assert store_calls == []
    assert create_calls == []
    assert dismiss_calls == []
