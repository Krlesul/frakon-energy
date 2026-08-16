from dataclasses import replace
from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys

import pytest


def _load():
    name = "custom_components.frakon_energy.legacy_tariff_history"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        name,
        Path("custom_components/frakon_energy/legacy_tariff_history.py"),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _options():
    return {
        "price_vt_czk_kwh": 7.52,
        "price_nt_czk_kwh": "4.67",
        "fixed_monthly_czk": Decimal("315.40"),
    }


def _snapshot(history, *, valid_from=date(2025, 2, 1), valid_to=date(2025, 12, 31)):
    return history.legacy_tariff_snapshot_from_options(
        _options(),
        valid_from=valid_from,
        valid_to=valid_to,
    )


def test_server_side_legacy_options_build_explicit_non_provenanced_snapshot() -> None:
    history = _load()
    snapshot = _snapshot(history)
    payload = snapshot.as_dict()

    assert snapshot.high_rate_czk_per_kwh == Decimal("7.52")
    assert snapshot.low_rate_czk_per_kwh == Decimal("4.67")
    assert snapshot.fixed_monthly_czk == Decimal("315.40")
    assert payload["source"] == "legacy_options"
    assert payload["authority_method"] == "legacy_manual_import"
    assert payload["component_breakdown_available"] is False
    assert payload["official_provenance_available"] is False
    assert payload["confirmed"] is False


def test_legacy_price_options_must_be_complete_and_non_negative() -> None:
    history = _load()
    with pytest.raises(LookupError, match="not available"):
        history.legacy_price_values_from_options({})
    with pytest.raises(ValueError, match="incomplete"):
        history.legacy_price_values_from_options({"price_vt_czk_kwh": 7.52})
    with pytest.raises(ValueError, match="finite and non-negative"):
        history.legacy_price_values_from_options(
            {
                "price_vt_czk_kwh": -1,
                "price_nt_czk_kwh": 4.67,
                "fixed_monthly_czk": 300,
            }
        )


def test_fingerprint_ignores_confirmation_and_append_is_idempotent() -> None:
    history = _load()
    snapshot = _snapshot(history)
    confirmed = replace(snapshot, confirmed=True)

    assert history.legacy_tariff_fingerprint(snapshot) == history.legacy_tariff_fingerprint(
        confirmed
    )
    once = history.append_legacy_tariff_snapshot(_options(), snapshot)
    twice = history.append_legacy_tariff_snapshot(once, snapshot)
    assert twice == once
    assert len(history.legacy_tariff_history_from_options(twice)) == 1


def test_confirmation_is_exact_idempotent_and_preserves_legacy_price_options() -> None:
    history = _load()
    snapshot = _snapshot(history)
    options = history.append_legacy_tariff_snapshot(_options(), snapshot)
    fingerprint = history.legacy_tariff_fingerprint(snapshot)

    confirmed = history.confirm_legacy_tariff_snapshot(options, fingerprint)
    repeated = history.confirm_legacy_tariff_snapshot(confirmed, fingerprint)

    assert repeated == confirmed
    stored = history.legacy_tariff_history_from_options(confirmed)
    assert stored == (replace(snapshot, confirmed=True),)
    for key, value in _options().items():
        assert confirmed[key] == value

    with pytest.raises(LookupError, match="not found"):
        history.confirm_legacy_tariff_snapshot(confirmed, "0" * 64)


def test_overlapping_confirmed_legacy_windows_fail_closed() -> None:
    history = _load()
    first = _snapshot(
        history,
        valid_from=date(2025, 1, 1),
        valid_to=date(2025, 6, 30),
    )
    second = _snapshot(
        history,
        valid_from=date(2025, 6, 1),
        valid_to=date(2025, 12, 31),
    )
    options = history.append_legacy_tariff_snapshot(_options(), first)
    options = history.append_legacy_tariff_snapshot(options, second)
    options = history.confirm_legacy_tariff_snapshot(
        options,
        history.legacy_tariff_fingerprint(first),
    )

    with pytest.raises(ValueError, match="overlaps confirmed"):
        history.confirm_legacy_tariff_snapshot(
            options,
            history.legacy_tariff_fingerprint(second),
        )


def test_selector_requires_exactly_one_confirmed_snapshot_for_day() -> None:
    history = _load()
    first = replace(
        _snapshot(history, valid_from=date(2025, 1, 1), valid_to=date(2025, 12, 31)),
        confirmed=True,
    )
    second = replace(
        _snapshot(history, valid_from=date(2025, 6, 1), valid_to=date(2025, 8, 31)),
        confirmed=True,
    )

    assert history.select_confirmed_legacy_tariff_for_day(
        (first,),
        date(2025, 5, 1),
    ) == first
    with pytest.raises(LookupError, match="No confirmed legacy tariff"):
        history.select_confirmed_legacy_tariff_for_day((first,), date(2026, 1, 1))
    with pytest.raises(ValueError, match="ambiguous confirmed legacy tariffs"):
        history.select_confirmed_legacy_tariff_for_day(
            (first, second),
            date(2025, 7, 1),
        )


def test_legacy_history_rejects_claimed_component_or_official_provenance() -> None:
    history = _load()
    payload = _snapshot(history).as_dict()
    payload["component_breakdown_available"] = True
    with pytest.raises(ValueError, match="cannot claim a component breakdown"):
        history.LegacyTariffSnapshot.from_dict(payload)

    payload = _snapshot(history).as_dict()
    payload["official_provenance_available"] = True
    with pytest.raises(ValueError, match="cannot claim official provenance"):
        history.LegacyTariffSnapshot.from_dict(payload)
