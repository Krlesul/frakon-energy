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
    # The advance starts on 27 January, so the first complete scheduled month
    # is February. Through 10 July, six monthly advances have been counted.
    assert snapshot.paid_advances_czk == Decimal("30000.00")
    assert snapshot.current_balance_czk == Decimal("5000.00")
    assert snapshot.projected_total_advances_czk == Decimal("60000.00")
    assert snapshot.projected_settlement_balance_czk == Decimal("2000.00")


def test_default_settlement_date_is_next_31_january() -> None:
    billing = load_module("billing")
    assert billing.next_default_settlement_date(date(2026, 1, 20)) == date(2026, 1, 31)
    assert billing.next_default_settlement_date(date(2026, 2, 1)) == date(2027, 1, 31)


def test_primary_entry_websocket_is_loaded_visionq_authority() -> None:
    source = (ROOT / "technology_profile_ws_api.py").read_text(encoding="utf-8")
    assert 'COMMAND_PRIMARY_ENTRY = "frakon_energy/entry/primary"' in source
    assert "PROVIDER_VISIONQ" in source
    assert "entry.entry_id in domain_data" in source
    assert "visionq_runtime_unavailable" in source
    assert "ambiguous_visionq_runtime" in source


def test_entity_discovery_missing_runtime_has_actionable_websocket_error() -> None:
    source = (ROOT / "entity_discovery_ws_api.py").read_text(encoding="utf-8")
    assert "entity_discovery_runtime_unavailable" in source
    assert "_send_runtime_error" in source


def test_commissioning_form_uses_canonical_string_phase_values() -> None:
    source = (ROOT / "config_flow.py").read_text(encoding="utf-8")
    assert 'vol.In({"1": "1", "3": "3"})' in source
    assert "default=str(defaults[CONF_CONTRACT_BREAKER_PHASES])" in source
    assert 'CONF_CONTRACT_BREAKER_PHASES: "3"' in source


def test_billing_advance_cannot_outlive_its_settlement_cycle() -> None:
    source = (ROOT / "config_flow.py").read_text(encoding="utf-8")
    assert "advance_end_after_settlement" in source
    assert "advance_to > settlement" in source


def test_removed_active_connection_require_admin_is_not_used() -> None:
    """Current Home Assistant ActiveConnection has no require_admin instance API."""
    violations = []
    for path in ROOT.rglob("*.py"):
        if path.name == "ws_auth.py":
            continue
        if "connection.require_admin()" in path.read_text(encoding="utf-8"):
            violations.append(str(path.relative_to(ROOT)))

    assert not violations, (
        "Removed Home Assistant websocket admin API is still used: "
        + ", ".join(violations)
    )


def test_websocket_admin_guard_matches_current_home_assistant_contract() -> None:
    source = (ROOT / "ws_auth.py").read_text(encoding="utf-8")
    assert "connection.require_admin()" not in source
    assert "user.is_admin" in source
    assert "Unauthorized" in source
