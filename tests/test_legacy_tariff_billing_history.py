from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys

import pytest


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _fixture(*, confirmed_legacy=True, legacy_valid_to=date(2026, 12, 31)):
    history_test = _load(
        "_frakon_test_legacy_tariff_billing_history_fixture",
        "tests/test_billing_all_in_history.py",
    )
    history, authority, _first, second, _proposal, second_fp, options = (
        history_test._fixture()
    )
    all_in = sys.modules["custom_components.frakon_energy.all_in_catalog"]
    legacy = _load(
        "custom_components.frakon_energy.legacy_tariff_history",
        "custom_components/frakon_energy/legacy_tariff_history.py",
    )

    # Keep only the confirmed new-model version that starts on 2026-07-01.  The
    # first half of the billing cycle therefore has a genuine historical gap.
    second_item = next(
        item
        for item in all_in.all_in_tariffs_from_options(options)
        if all_in.all_in_tariff_fingerprint(item) == second_fp
    )
    options = dict(options)
    options[all_in.OPTION_ALL_IN_TARIFF_CATALOG] = [second_item.as_dict()]
    options[authority.OPTION_ALL_IN_TARIFF_AUTHORITIES] = [
        item
        for item in options[authority.OPTION_ALL_IN_TARIFF_AUTHORITIES]
        if item["all_in_tariff_fingerprint"] == second_fp
    ]
    options.update(
        {
            legacy.LEGACY_PRICE_VT_OPTION: "7.52",
            legacy.LEGACY_PRICE_NT_OPTION: "4.67",
            legacy.LEGACY_FIXED_MONTHLY_OPTION: "315.40",
        }
    )
    snapshot = legacy.legacy_tariff_snapshot_from_options(
        options,
        valid_from=date(2026, 1, 1),
        valid_to=legacy_valid_to,
    )
    options = legacy.append_legacy_tariff_snapshot(options, snapshot)
    if confirmed_legacy:
        options = legacy.confirm_legacy_tariff_snapshot(
            options,
            legacy.legacy_tariff_fingerprint(snapshot),
        )
    return history, authority, all_in, legacy, second, second_fp, snapshot, options


def test_confirmed_legacy_snapshot_fills_only_pre_catalog_gap() -> None:
    history, _authority, _all_in, legacy, second, second_fp, snapshot, options = _fixture()

    schedule = history.confirmed_all_in_billing_schedule(
        options,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
    )

    assert len(schedule) == 2
    old, new = schedule
    assert old.source == history.SOURCE_CONFIRMED_LEGACY_HISTORY
    assert old.valid_from == date(2026, 1, 1)
    assert old.valid_to == date(2026, 6, 30)
    assert old.all_in_tariff_fingerprint is None
    assert old.legacy_tariff_fingerprint == legacy.legacy_tariff_fingerprint(snapshot)
    assert old.authority_method == legacy.LegacyTariffAuthorityMethod.LEGACY_MANUAL_IMPORT.value
    assert old.supplier is None
    assert old.product_name is None
    assert old.prices.high_rate_czk_per_kwh == Decimal("7.52")
    assert old.prices.low_rate_czk_per_kwh == Decimal("4.67")
    assert old.prices.fixed_monthly_czk == Decimal("315.40")

    assert new.source == history.SOURCE_CONFIRMED_ALL_IN
    assert new.valid_from == date(2026, 7, 1)
    assert new.valid_to == date(2026, 12, 31)
    assert new.all_in_tariff_fingerprint == second_fp
    assert new.legacy_tariff_fingerprint is None
    assert new.prices.high_rate_czk_per_kwh == second.all_in_vt_czk_kwh


def test_confirmed_all_in_always_wins_even_when_legacy_window_overlaps() -> None:
    history, _authority, _all_in, _legacy, second, second_fp, _snapshot, options = _fixture(
        legacy_valid_to=date(2026, 12, 31)
    )

    schedule = history.confirmed_all_in_billing_schedule(
        options,
        start_date=date(2026, 6, 30),
        end_date=date(2026, 7, 2),
    )

    assert len(schedule) == 2
    assert schedule[0].source == history.SOURCE_CONFIRMED_LEGACY_HISTORY
    assert schedule[0].valid_from == schedule[0].valid_to == date(2026, 6, 30)
    assert schedule[1].source == history.SOURCE_CONFIRMED_ALL_IN
    assert schedule[1].valid_from == date(2026, 7, 1)
    assert schedule[1].valid_to == date(2026, 7, 2)
    assert schedule[1].all_in_tariff_fingerprint == second_fp
    assert schedule[1].prices.high_rate_czk_per_kwh == second.all_in_vt_czk_kwh


def test_unconfirmed_legacy_snapshot_never_fills_missing_new_catalog_day() -> None:
    history, _authority, _all_in, _legacy, _second, _second_fp, _snapshot, options = _fixture(
        confirmed_legacy=False
    )

    with pytest.raises(LookupError, match="No confirmed all-in tariff applies"):
        history.confirmed_all_in_billing_schedule(
            options,
            start_date=date(2026, 6, 30),
            end_date=date(2026, 7, 1),
        )


def test_missing_new_all_in_authority_is_never_hidden_by_overlapping_legacy_history() -> None:
    history, authority, _all_in, _legacy, _second, _second_fp, _snapshot, options = _fixture()
    broken = dict(options)
    broken[authority.OPTION_ALL_IN_TARIFF_AUTHORITIES] = []

    with pytest.raises(LookupError, match="all-in tariff authority not found"):
        history.confirmed_all_in_billing_schedule(
            broken,
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 1),
        )


def test_mixed_projection_marks_historical_migration_and_counts_both_versions() -> None:
    history, _authority, _all_in, _legacy, _second, _second_fp, _snapshot, options = _fixture()

    result = history.calculate_confirmed_all_in_cost_projection(
        options,
        cycle_start=date(2026, 1, 1),
        settlement_date=date(2026, 12, 31),
        as_of=date(2026, 9, 30),
        baseline_high_rate_kwh=Decimal("0"),
        baseline_low_rate_kwh=Decimal("0"),
        current_high_rate_kwh=Decimal("273"),
        current_low_rate_kwh=Decimal("546"),
    )

    assert result.method == "daily_confirmed_mixed_tariff_history_linear_consumption"
    assert result.tariff_version_count == 2
    assert len(result.segments) == 2
    assert result.segments[0].source == history.SOURCE_CONFIRMED_LEGACY_HISTORY
    assert result.segments[1].source == history.SOURCE_CONFIRMED_ALL_IN
