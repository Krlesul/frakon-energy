from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _fixture():
    history_test = _load(
        "_frakon_test_billing_history_fixture",
        "tests/test_billing_all_in_history.py",
    )
    history, _authority, _first, _second, _proposal, _second_fp, options = (
        history_test._fixture()
    )
    cost = sys.modules["custom_components.frakon_energy.cost"]
    selector = _load(
        "custom_components.frakon_energy.billing_tariff_selection",
        "custom_components/frakon_energy/billing_tariff_selection.py",
    )
    return history, cost, selector, options


def test_confirmed_catalog_selection_carries_read_only_schedule_snapshot() -> None:
    _history, cost, selector, options = _fixture()

    selected = selector.select_billing_tariff_prices(
        options,
        day=date(2026, 9, 30),
    )

    assert selected.source == "confirmed_all_in"
    assert isinstance(selected.prices, cost.CatalogTariffPrices)
    assert selected.prices.catalog_options is not options
    assert selected.prices.catalog_options["electricity_contracts"] == options["electricity_contracts"]


def test_stable_cost_api_routes_catalog_prices_to_historical_projection() -> None:
    history, cost, selector, options = _fixture()
    selected = selector.select_billing_tariff_prices(
        options,
        day=date(2026, 9, 30),
    )
    kwargs = {
        "cycle_start": date(2026, 1, 1),
        "settlement_date": date(2026, 12, 31),
        "as_of": date(2026, 9, 30),
        "baseline_high_rate_kwh": Decimal("0"),
        "baseline_low_rate_kwh": Decimal("0"),
        "current_high_rate_kwh": Decimal("273"),
        "current_low_rate_kwh": Decimal("546"),
    }

    routed = cost.calculate_cost_projection(
        **kwargs,
        prices=selected.prices,
    )
    direct = history.calculate_confirmed_all_in_cost_projection(
        options,
        **kwargs,
    ).cost

    assert routed == direct


def test_plain_legacy_tariff_prices_keep_original_single_price_projection() -> None:
    _history, cost, _selector, _options = _fixture()
    prices = cost.TariffPrices(
        high_rate_czk_per_kwh=Decimal("5"),
        low_rate_czk_per_kwh=Decimal("4"),
        fixed_monthly_czk=Decimal("120"),
    )

    result = cost.calculate_cost_projection(
        cycle_start=date(2026, 1, 1),
        settlement_date=date(2026, 12, 31),
        as_of=date(2026, 6, 30),
        baseline_high_rate_kwh=Decimal("0"),
        baseline_low_rate_kwh=Decimal("0"),
        current_high_rate_kwh=Decimal("181"),
        current_low_rate_kwh=Decimal("362"),
        prices=prices,
    )

    assert result.accrued_energy_cost_czk == Decimal("2353.00")
    assert result.accrued_fixed_cost_czk == Decimal("714.08")
    assert result.projected_total_cost_czk == Decimal("6185.00")
