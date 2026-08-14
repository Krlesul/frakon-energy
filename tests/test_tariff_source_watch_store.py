from datetime import date, datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import types


def load_modules():
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.pricing",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components.frakon_energy.tariff_assembly",
        "custom_components.frakon_energy.all_in_catalog",
        "custom_components.frakon_energy.tariff_source_watch",
        "custom_components.frakon_energy.tariff_source_watch_store",
    )
    for name in names:
        sys.modules.pop(name, None)

    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, Path(path))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    load(
        "custom_components.frakon_energy.pricing",
        "custom_components/frakon_energy/pricing.py",
    )
    sources = load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    load(
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components/frakon_energy/tariff_provenance.py",
    )
    load(
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components/frakon_energy/regulated_pricing.py",
    )
    load(
        "custom_components.frakon_energy.tariff_assembly",
        "custom_components/frakon_energy/tariff_assembly.py",
    )
    load(
        "custom_components.frakon_energy.all_in_catalog",
        "custom_components/frakon_energy/all_in_catalog.py",
    )
    watch = load(
        "custom_components.frakon_energy.tariff_source_watch",
        "custom_components/frakon_energy/tariff_source_watch.py",
    )
    store = load(
        "custom_components.frakon_energy.tariff_source_watch_store",
        "custom_components/frakon_energy/tariff_source_watch_store.py",
    )
    return sources, watch, store


def _watch(watch, *, active="a", etag='"v1"'):
    return watch.TariffSourceWatch(
        supplier="cez",
        product_name="Basic",
        source_name="ČEZ Prodej",
        document_name="Basic 2026",
        source_url="https://www.cez.cz/file/edee/basic-2026.pdf",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        active_sha256=active * 64,
        document_date=date(2025, 10, 1),
        etag=etag,
        last_modified="Thu, 01 Jan 2026 00:00:00 GMT",
    )


def _t(hour=8):
    return datetime(2026, 8, 14, hour, 0, tzinfo=timezone.utc)


def test_append_preserves_unrelated_options_is_idempotent_and_never_replaces_active_hash() -> None:
    _, watch, store = load_modules()
    source_watch = _watch(watch)
    options = {"unrelated": {"keep": True}, "all_in_tariff_catalog": ["history"]}

    once = store.append_tariff_source_watch(options, source_watch)
    twice = store.append_tariff_source_watch(once, source_watch)

    assert twice == once
    assert twice["unrelated"] == {"keep": True}
    assert twice["all_in_tariff_catalog"] == ["history"]
    records = store.tariff_source_watch_records_from_options(twice)
    assert len(records) == 1
    assert records[0].watch == source_watch
    assert records[0].last_check is None
    assert records[0].pending_sha256 is None

    same_target_new_active = _watch(watch, active="b")
    try:
        store.append_tariff_source_watch(twice, same_target_new_active)
    except ValueError as err:
        assert "cannot be replaced" in str(err)
    else:
        raise AssertionError("Append must never replace active source checksum")


def test_change_detection_persists_pending_without_mutating_active_hash() -> None:
    sources, watch, store = load_modules()
    source_watch = _watch(watch)
    options = store.append_tariff_source_watch({}, source_watch)
    changed_document = sources.OfficialTariffDocument(
        supplier="cez",
        source_url=source_watch.source_url,
        discovered_at=_t(),
        sha256="b" * 64,
        etag='"v2"',
        last_modified="Fri, 14 Aug 2026 06:00:00 GMT",
        content_type="application/pdf",
    )
    result = watch.evaluate_tariff_source_download(
        source_watch,
        document=changed_document,
        checked_at=_t(),
    )

    updated = store.record_tariff_source_check(options, result)
    record = store.tariff_source_watch_record_from_options(
        updated,
        watch.tariff_source_watch_fingerprint(source_watch),
    )

    assert record.watch.active_sha256 == "a" * 64
    assert record.watch.etag == '"v2"'
    assert record.last_check.status == watch.STATUS_CHANGE_DETECTED
    assert record.pending_sha256 == "b" * 64
    assert record.pending_detected_at == _t()
    assert record.last_check.requires_confirmation is True


def test_not_modified_and_error_preserve_pending_proposal_across_restarts() -> None:
    sources, watch, store = load_modules()
    source_watch = _watch(watch)
    options = store.append_tariff_source_watch({}, source_watch)
    changed = watch.evaluate_tariff_source_download(
        source_watch,
        document=sources.OfficialTariffDocument(
            supplier="cez",
            source_url=source_watch.source_url,
            discovered_at=_t(),
            sha256="b" * 64,
            etag='"v2"',
        ),
        checked_at=_t(),
    )
    options = store.record_tariff_source_check(options, changed)
    record = store.tariff_source_watch_records_from_options(options)[0]

    not_modified = watch.tariff_source_not_modified(
        record.watch,
        checked_at=_t() + timedelta(days=7),
        etag='"v2"',
    )
    options = store.record_tariff_source_check(options, not_modified)
    reloaded = store.tariff_source_watch_records_from_options(options)[0]
    assert reloaded.pending_sha256 == "b" * 64
    assert reloaded.pending_detected_at == _t()
    assert reloaded.last_check.status == watch.STATUS_NOT_MODIFIED
    assert reloaded.watch.active_sha256 == "a" * 64

    error = watch.tariff_source_check_error(
        reloaded.watch,
        checked_at=_t() + timedelta(days=14),
        error="timeout",
    )
    options = store.record_tariff_source_check(options, error)
    after_error = store.tariff_source_watch_records_from_options(options)[0]
    assert after_error.pending_sha256 == "b" * 64
    assert after_error.last_check.status == watch.STATUS_ERROR
    assert after_error.last_check.error == "timeout"
    assert after_error.watch.active_sha256 == "a" * 64


def test_observing_active_hash_again_clears_stale_pending_proposal() -> None:
    sources, watch, store = load_modules()
    source_watch = _watch(watch)
    options = store.append_tariff_source_watch({}, source_watch)
    changed = watch.evaluate_tariff_source_download(
        source_watch,
        document=sources.OfficialTariffDocument(
            supplier="cez",
            source_url=source_watch.source_url,
            discovered_at=_t(),
            sha256="b" * 64,
            etag='"v2"',
        ),
        checked_at=_t(),
    )
    options = store.record_tariff_source_check(options, changed)
    current = store.tariff_source_watch_records_from_options(options)[0]

    reverted = watch.evaluate_tariff_source_download(
        current.watch,
        document=sources.OfficialTariffDocument(
            supplier="cez",
            source_url=current.watch.source_url,
            discovered_at=_t() + timedelta(days=7),
            sha256="a" * 64,
            etag='"v1-again"',
        ),
        checked_at=_t() + timedelta(days=7),
    )
    options = store.record_tariff_source_check(options, reverted)
    record = store.tariff_source_watch_records_from_options(options)[0]

    assert record.last_check.status == watch.STATUS_UNCHANGED_HASH
    assert record.pending_sha256 is None
    assert record.pending_detected_at is None
    assert record.watch.active_sha256 == "a" * 64


def test_store_round_trip_duplicate_corruption_and_unknown_check_fail_closed() -> None:
    _, watch, store = load_modules()
    source_watch = _watch(watch)
    record = store.TariffSourceWatchRecord(watch=source_watch)
    restored = store.TariffSourceWatchRecord.from_dict(record.as_dict())
    assert restored == record

    duplicate = {
        store.OPTION_TARIFF_SOURCE_WATCHES: [record.as_dict(), record.as_dict()]
    }
    try:
        store.tariff_source_watch_records_from_options(duplicate)
    except ValueError as err:
        assert "duplicate tariff source watch fingerprint" in str(err)
    else:
        raise AssertionError("Duplicate durable watch target must fail closed")

    unknown_result = watch.tariff_source_check_error(
        source_watch,
        checked_at=_t(),
        error="offline",
    )
    try:
        store.record_tariff_source_check({}, unknown_result)
    except LookupError:
        pass
    else:
        raise AssertionError("Check for unknown watch target must fail closed")

    payload = record.as_dict()
    payload["schema_version"] = 999
    try:
        store.TariffSourceWatchRecord.from_dict(payload)
    except ValueError as err:
        assert "unsupported tariff source watch store schema" in str(err)
    else:
        raise AssertionError("Unknown durable watch schema must fail closed")
