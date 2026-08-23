from datetime import date
import importlib.util
from pathlib import Path
import sys


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_daily_costs_support_mixed_legacy_and_all_in_authority_methods() -> None:
    legacy_history_test = _load(
        "_frakon_test_daily_costs_legacy_fixture",
        "tests/test_legacy_tariff_billing_history.py",
    )
    (
        _history,
        _authority,
        _all_in,
        legacy,
        second,
        second_fp,
        snapshot,
        options,
    ) = legacy_history_test._fixture(legacy_valid_to=date(2026, 6, 30))

    daily = _load(
        "custom_components.frakon_energy.daily_all_in_costs",
        "custom_components/frakon_energy/daily_all_in_costs.py",
    )
    records = daily.price_confirmed_daily_consumption(
        options,
        (
            {"day": "2026-06-30", "high_rate_kwh": "2", "low_rate_kwh": "3"},
            {"day": "2026-07-01", "high_rate_kwh": "4", "low_rate_kwh": "5"},
        ),
    )

    assert len(records) == 2
    old, new = records

    assert old.authority_method == legacy.LegacyTariffAuthorityMethod.LEGACY_MANUAL_IMPORT.value
    assert old.all_in_tariff_fingerprint is None
    assert old.supplier is None
    assert old.product_name is None
    assert old.high_rate_czk_per_kwh == snapshot.high_rate_czk_per_kwh
    assert old.low_rate_czk_per_kwh == snapshot.low_rate_czk_per_kwh
    assert old.as_dict()["authority_method"] == "legacy_manual_import"

    assert new.all_in_tariff_fingerprint == second_fp
    assert isinstance(new.authority_method, str)
    assert new.high_rate_czk_per_kwh == second.all_in_vt_czk_kwh
    assert new.low_rate_czk_per_kwh == second.all_in_nt_czk_kwh
