import importlib.util
from pathlib import Path
import sys
import types


def load_module():
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.tariff_source_watch",
        "custom_components.frakon_energy.tariff_source_watch_store",
        "custom_components.frakon_energy.tariff_update_notifications",
    )
    for name in names:
        sys.modules.pop(name, None)

    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

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

    spec = importlib.util.spec_from_file_location(
        "custom_components.frakon_energy.tariff_update_notifications",
        Path("custom_components/frakon_energy/tariff_update_notifications.py"),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _watch():
    return types.SimpleNamespace(
        fingerprint="f" * 64,
        product_name="Basic",
        source_name="ČEZ Prodej",
        document_name="Basic 2026",
        source_url="https://www.cez.cz/file/basic.pdf",
    )


def _run(*, status="change_detected", observed="b" * 64, requires_confirmation=True):
    watch = _watch()
    return types.SimpleNamespace(
        activation_performed=False,
        prepared=types.SimpleNamespace(record=types.SimpleNamespace(watch=watch)),
        check=types.SimpleNamespace(
            status=status,
            observed_sha256=observed,
            requires_confirmation=requires_confirmation,
            watch_fingerprint=watch.fingerprint,
        ),
    )


def test_pending_snapshot_is_keyed_by_stable_watch_identity() -> None:
    module = load_module()
    records = (
        types.SimpleNamespace(watch=_watch(), pending_sha256="b" * 64),
    )

    assert module.pending_tariff_hashes({"records": records}) == {
        "f" * 64: "b" * 64
    }


def test_new_change_creates_review_notification_without_activation_language() -> None:
    module = load_module()
    notification = module.notification_for_new_pending_tariff(
        _run(), pending_before={"f" * 64: None}
    )

    assert notification is not None
    assert notification.observed_sha256 == "b" * 64
    assert notification.title == "FRAKON Energy: tariff update available"
    assert "Basic" in notification.message
    assert "ČEZ Prodej" in notification.message
    assert "active tariff has not changed" in notification.message
    assert "reviewed and confirmed" in notification.message


def test_same_pending_hash_is_not_notified_again_after_restart() -> None:
    module = load_module()
    notification = module.notification_for_new_pending_tariff(
        _run(), pending_before={"f" * 64: "b" * 64}
    )

    assert notification is None


def test_non_change_and_non_confirmation_results_never_notify() -> None:
    module = load_module()
    for run in (
        _run(status="not_modified", observed=None, requires_confirmation=False),
        _run(status="unchanged_hash", observed="a" * 64, requires_confirmation=False),
        _run(status="error", observed=None, requires_confirmation=False),
        _run(status="change_detected", observed="b" * 64, requires_confirmation=False),
    ):
        assert module.notification_for_new_pending_tariff(
            run, pending_before={}
        ) is None


def test_notification_refuses_run_with_activation_authority() -> None:
    module = load_module()
    run = _run()
    run.activation_performed = True

    try:
        module.notification_for_new_pending_tariff(run, pending_before={})
    except ValueError as err:
        assert "non-activating" in str(err)
    else:
        raise AssertionError("Activating run must never create update notification")
