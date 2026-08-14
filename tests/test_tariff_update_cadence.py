from datetime import date, datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import types


def load_module(*, last_checked_at):
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.tariff_update_orchestrator",
        "custom_components.frakon_energy.tariff_update_cadence",
    )
    for name in names:
        sys.modules.pop(name, None)

    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    orchestrator = types.ModuleType(
        "custom_components.frakon_energy.tariff_update_orchestrator"
    )
    prepare_calls = []

    class PreparedActiveTariffSourceWatch:
        def __init__(self, checked_at):
            last_check = None
            if checked_at is not None:
                last_check = types.SimpleNamespace(checked_at=checked_at)
            self.record = types.SimpleNamespace(last_check=last_check)

    prepared = PreparedActiveTariffSourceWatch(last_checked_at)

    def prepare_active_tariff_source_watch(options, *, day):
        prepare_calls.append((options, day))
        return prepared

    orchestrator.PreparedActiveTariffSourceWatch = PreparedActiveTariffSourceWatch
    orchestrator.prepare_active_tariff_source_watch = prepare_active_tariff_source_watch
    sys.modules[orchestrator.__name__] = orchestrator

    spec = importlib.util.spec_from_file_location(
        "custom_components.frakon_energy.tariff_update_cadence",
        Path("custom_components/frakon_energy/tariff_update_cadence.py"),
    )
    cadence = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = cadence
    spec.loader.exec_module(cadence)
    return cadence, prepare_calls, prepared


def test_never_checked_active_source_is_due_immediately() -> None:
    cadence, prepare_calls, prepared = load_module(last_checked_at=None)
    options = {"existing": True}
    day = date(2026, 8, 14)
    checked_at = datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc)

    result = cadence.active_tariff_check_cadence(
        options,
        day=day,
        checked_at=checked_at,
    )

    assert result.prepared is prepared
    assert result.due is True
    assert result.last_checked_at is None
    assert result.next_due_at is None
    assert result.interval == timedelta(days=7)
    assert prepare_calls == [(options, day)]


def test_active_source_is_not_due_before_full_week() -> None:
    last_checked_at = datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)
    cadence, _prepare_calls, _prepared = load_module(
        last_checked_at=last_checked_at
    )

    result = cadence.active_tariff_check_cadence(
        {},
        day=date(2026, 8, 14),
        checked_at=datetime(2026, 8, 14, 9, 59, 59, tzinfo=timezone.utc),
    )

    assert result.due is False
    assert result.last_checked_at == last_checked_at
    assert result.next_due_at == datetime(
        2026, 8, 14, 10, 0, tzinfo=timezone.utc
    )


def test_active_source_is_due_at_exact_week_boundary() -> None:
    last_checked_at = datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)
    cadence, _prepare_calls, _prepared = load_module(
        last_checked_at=last_checked_at
    )

    result = cadence.active_tariff_check_cadence(
        {},
        day=date(2026, 8, 14),
        checked_at=datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc),
    )

    assert result.due is True
    assert result.next_due_at == datetime(
        2026, 8, 14, 10, 0, tzinfo=timezone.utc
    )


def test_custom_interval_is_honored_without_changing_default() -> None:
    last_checked_at = datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc)
    cadence, _prepare_calls, _prepared = load_module(
        last_checked_at=last_checked_at
    )

    result = cadence.active_tariff_check_cadence(
        {},
        day=date(2026, 8, 14),
        checked_at=datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc),
        interval=timedelta(days=3),
    )

    assert result.due is True
    assert result.interval == timedelta(days=3)
    assert cadence.DEFAULT_TARIFF_UPDATE_INTERVAL == timedelta(days=7)


def test_invalid_timestamp_and_interval_fail_before_authority_resolution() -> None:
    cadence, prepare_calls, _prepared = load_module(last_checked_at=None)

    try:
        cadence.active_tariff_check_cadence(
            {},
            day=date(2026, 8, 14),
            checked_at=datetime(2026, 8, 14, 10, 0),
        )
    except ValueError as err:
        assert "timezone-aware" in str(err)
    else:
        raise AssertionError("Naive cadence timestamp must fail closed")

    try:
        cadence.active_tariff_check_cadence(
            {},
            day=date(2026, 8, 14),
            checked_at=datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc),
            interval=timedelta(0),
        )
    except ValueError as err:
        assert "positive timedelta" in str(err)
    else:
        raise AssertionError("Non-positive cadence interval must fail closed")

    assert prepare_calls == []
