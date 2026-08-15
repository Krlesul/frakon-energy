from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
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


def _fixture():
    history_test = _load(
        "_frakon_test_daily_all_in_history_fixture",
        "tests/test_billing_all_in_history.py",
    )
    history, authority, first, second, proposal, second_fp, options = (
        history_test._fixture()
    )
    daily = _load(
        "custom_components.frakon_energy.daily_all_in_costs",
        "custom_components/frakon_energy/daily_all_in_costs.py",
    )
    return daily, authority, first, second, proposal, second_fp, options


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def test_daily_costs_use_exact_tariff_version_on_each_measurement_day() -> None:
    daily, authority, first, second, proposal, second_fp, options = _fixture()
    records = daily.price_confirmed_daily_consumption(
        options,
        (
            {"day": "2026-06-30", "high_rate_kwh": "2", "low_rate_kwh": "3"},
            {"day": date(2026, 7, 1), "high_rate_kwh": Decimal("4"), "low_rate_kwh": Decimal("5")},
        ),
    )

    assert len(records) == 2
    old, new = records
    assert old.all_in_tariff_fingerprint == proposal.all_in_tariff_fingerprint
    assert old.high_rate_czk_per_kwh == first.all_in_vt_czk_kwh
    assert old.low_rate_czk_per_kwh == first.all_in_nt_czk_kwh
    assert old.variable_cost_czk == _money(
        Decimal("2") * first.all_in_vt_czk_kwh
        + Decimal("3") * first.all_in_nt_czk_kwh
    )
    assert old.authority_method == authority.AllInTariffAuthorityMethod.VERIFIED_PARSER.value

    assert new.all_in_tariff_fingerprint == second_fp
    assert new.high_rate_czk_per_kwh == second.all_in_vt_czk_kwh
    assert new.low_rate_czk_per_kwh == second.all_in_nt_czk_kwh
    assert new.variable_cost_czk == _money(
        Decimal("4") * second.all_in_vt_czk_kwh
        + Decimal("5") * second.all_in_nt_czk_kwh
    )
    assert old.fixed_monthly_excluded is True
    assert new.fixed_monthly_excluded is True


def test_serialized_daily_cost_never_claims_fixed_fee_or_effective_price() -> None:
    daily, _authority, _first, _second, _proposal, _second_fp, options = _fixture()
    record = daily.price_confirmed_daily_consumption(
        options,
        ({"day": "2026-06-30", "high_rate_kwh": "1.2345", "low_rate_kwh": "2.3456"},),
    )[0]

    payload = record.as_dict()
    assert payload["fixed_monthly_excluded"] is True
    assert payload["high_rate_kwh"] == "1.234"
    assert payload["low_rate_kwh"] == "2.346"
    assert payload["total_kwh"] == "3.580"
    assert "fixed_cost_czk" not in payload
    assert "effective_czk_kwh" not in payload


def test_summary_is_variable_only_and_matches_daily_records() -> None:
    daily, _authority, _first, _second, _proposal, _second_fp, options = _fixture()
    records = daily.price_confirmed_daily_consumption(
        options,
        (
            {"day": "2026-06-30", "high_rate_kwh": "2", "low_rate_kwh": "3"},
            {"day": "2026-07-01", "high_rate_kwh": "4", "low_rate_kwh": "5"},
        ),
    )
    summary = daily.summarize_daily_all_in_costs(records)

    assert summary == {
        "days": 2,
        "high_rate_kwh": "6.000",
        "low_rate_kwh": "8.000",
        "total_kwh": "14.000",
        "variable_cost_czk": str(_money(sum((item.variable_cost_czk for item in records), Decimal("0")))),
        "fixed_monthly_excluded": True,
    }


def test_duplicate_daily_measurements_fail_closed() -> None:
    daily, _authority, _first, _second, _proposal, _second_fp, options = _fixture()

    with pytest.raises(ValueError, match="duplicate calendar days"):
        daily.price_confirmed_daily_consumption(
            options,
            (
                {"day": "2026-06-30", "high_rate_kwh": "1", "low_rate_kwh": "1"},
                {"day": "2026-06-30", "high_rate_kwh": "2", "low_rate_kwh": "2"},
            ),
        )


def test_missing_authority_for_any_day_never_falls_back_to_legacy_price() -> None:
    daily, authority, _first, _second, _proposal, second_fp, options = _fixture()
    broken = deepcopy(options)
    broken[authority.OPTION_ALL_IN_TARIFF_AUTHORITIES] = [
        item
        for item in broken[authority.OPTION_ALL_IN_TARIFF_AUTHORITIES]
        if item["all_in_tariff_fingerprint"] != second_fp
    ]

    with pytest.raises(LookupError, match="all-in tariff authority not found"):
        daily.price_confirmed_daily_consumption(
            broken,
            (
                {"day": "2026-06-30", "high_rate_kwh": "1", "low_rate_kwh": "1"},
                {"day": "2026-07-01", "high_rate_kwh": "1", "low_rate_kwh": "1"},
            ),
        )


def test_empty_daily_series_is_safe_and_has_zero_variable_summary() -> None:
    daily, _authority, _first, _second, _proposal, _second_fp, options = _fixture()

    assert daily.price_confirmed_daily_consumption(options, ()) == ()
    assert daily.summarize_daily_all_in_costs(()) == {
        "days": 0,
        "high_rate_kwh": "0.000",
        "low_rate_kwh": "0.000",
        "total_kwh": "0.000",
        "variable_cost_czk": "0.00",
        "fixed_monthly_excluded": True,
    }
