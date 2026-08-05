from __future__ import annotations

import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1] / "custom_components" / "frakon_energy"


def load_module(name: str):
    module_name = f"frakon_energy_test_{name}"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_cost_projection_uses_vt_nt_and_fixed_fees() -> None:
    cost = load_module("cost")
    result = cost.calculate_cost_projection(
        cycle_start=date(2026, 1, 27),
        settlement_date=date(2027, 1, 31),
        as_of=date(2026, 7, 10),
        baseline_high_rate_kwh=Decimal("0"),
        baseline_low_rate_kwh=Decimal("0"),
        current_high_rate_kwh=Decimal("2022"),
        current_low_rate_kwh=Decimal("1526"),
        prices=cost.TariffPrices(
            high_rate_czk_per_kwh=Decimal("7.52"),
            low_rate_czk_per_kwh=Decimal("4.67"),
            fixed_monthly_czk=Decimal("300"),
        ),
    )
    assert result.high_rate_consumption_kwh == Decimal("2022.000")
    assert result.low_rate_consumption_kwh == Decimal("1526.000")
    assert result.accrued_total_cost_czk > Decimal("22000")
    assert result.projected_total_cost_czk >= result.accrued_total_cost_czk


def test_cost_projection_rejects_meter_reset() -> None:
    cost = load_module("cost")
    result = cost.calculate_cost_projection(
        cycle_start=date(2026, 1, 27),
        settlement_date=date(2027, 1, 31),
        as_of=date(2026, 7, 10),
        baseline_high_rate_kwh=Decimal("100"),
        baseline_low_rate_kwh=Decimal("200"),
        current_high_rate_kwh=Decimal("90"),
        current_low_rate_kwh=Decimal("190"),
        prices=cost.TariffPrices(Decimal("7.52"), Decimal("4.67"), Decimal("0")),
    )
    assert result.high_rate_consumption_kwh == Decimal("0.000")
    assert result.low_rate_consumption_kwh == Decimal("0.000")


def test_billing_snapshot_with_5000_czk_advance() -> None:
    billing = load_module("billing")
    cycle = billing.BillingCycle(
        start_date=date(2026, 1, 27),
        expected_settlement_date=date(2027, 1, 31),
        baseline=billing.MeterBaseline(
            reading_date=date(2026, 1, 27),
            high_rate_kwh=Decimal("0"),
            low_rate_kwh=Decimal("0"),
        ),
    )
    snapshot = billing.BillingCalculator.calculate(
        cycle=cycle,
        as_of=date(2026, 7, 10),
        advances=(
            billing.AdvancePeriod(
                valid_from=date(2026, 1, 27),
                monthly_amount_czk=Decimal("5000"),
            ),
        ),
        accrued_cost_czk=Decimal("25000"),
        projected_total_cost_czk=Decimal("58000"),
    )
    assert snapshot.paid_advances_czk == Decimal("35000.00")
    assert snapshot.current_balance_czk == Decimal("10000.00")
    assert snapshot.projected_total_advances_czk == Decimal("65000.00")
    assert snapshot.projected_settlement_balance_czk == Decimal("7000.00")


def test_default_settlement_date_is_next_31_january() -> None:
    billing = load_module("billing")
    assert billing.next_default_settlement_date(date(2026, 1, 20)) == date(2026, 1, 31)
    assert billing.next_default_settlement_date(date(2026, 2, 1)) == date(2027, 1, 31)
