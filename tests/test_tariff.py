from datetime import date
from decimal import Decimal

import pytest

from custom_components.frakon_energy.tariff import (
    ConsumptionSlice,
    TariffCalculator,
    TariffPeriod,
)


def cez_d25d_2026(**overrides):
    values = {
        "valid_from": date(2026, 1, 1),
        "valid_to": None,
        "supplier": "ČEZ Prodej",
        "product": "Elektřina na dobu neurčitou",
        "distribution_rate": "D25d",
        "high_rate_supply_czk_mwh": Decimal("3960.00"),
        "low_rate_supply_czk_mwh": Decimal("3700.00"),
        "high_rate_distribution_czk_mwh": Decimal("2725.46"),
        "low_rate_distribution_czk_mwh": Decimal("140.97"),
        "electricity_tax_czk_mwh": Decimal("34.24"),
        "system_services_czk_mwh": Decimal("198.73"),
        "poze_consumption_czk_mwh": Decimal("598.95"),
        "supplier_monthly_fee_czk": Decimal("146.41"),
        "breaker_monthly_fee_czk": Decimal("325.49"),
        "infrastructure_monthly_fee_czk": Decimal("15.57"),
    }
    values.update(overrides)
    return TariffPeriod(**values)


def test_cez_d25d_totals_include_all_variable_components():
    tariff = cez_d25d_2026()
    assert tariff.high_rate_total_czk_mwh == Decimal("7517.38")
    assert tariff.low_rate_total_czk_mwh == Decimal("4672.89")
    assert tariff.monthly_fixed_czk == Decimal("487.47")


def test_cost_calculation_from_daily_vt_nt_consumption():
    tariff = cez_d25d_2026()
    result = TariffCalculator.calculate(
        consumption=(
            ConsumptionSlice(date(2026, 2, 1), Decimal("10"), Decimal("20")),
            ConsumptionSlice(date(2026, 2, 2), Decimal("5"), Decimal("15")),
        ),
        tariffs=(tariff,),
    )

    assert result.high_rate_kwh == Decimal("15.000")
    assert result.low_rate_kwh == Decimal("35.000")
    assert result.variable_high_rate_czk == Decimal("112.76")
    assert result.variable_low_rate_czk == Decimal("163.55")
    assert result.fixed_czk == Decimal("487.47")
    assert result.total_czk == Decimal("763.78")


def test_tariff_change_inside_billing_cycle_uses_correct_prices():
    old = cez_d25d_2026(valid_to=date(2026, 6, 30))
    new = cez_d25d_2026(
        valid_from=date(2026, 7, 1),
        high_rate_supply_czk_mwh=Decimal("4200"),
        low_rate_supply_czk_mwh=Decimal("3900"),
    )
    result = TariffCalculator.calculate(
        consumption=(
            ConsumptionSlice(date(2026, 6, 30), Decimal("10"), Decimal("10")),
            ConsumptionSlice(date(2026, 7, 1), Decimal("10"), Decimal("10")),
        ),
        tariffs=(old, new),
    )

    assert result.fixed_czk == Decimal("974.94")
    assert result.total_czk == Decimal("1223.15")


def test_missing_tariff_is_rejected_instead_of_silently_estimating():
    with pytest.raises(ValueError, match="No tariff configured"):
        TariffCalculator.calculate(
            consumption=(ConsumptionSlice(date(2025, 12, 31), Decimal("1"), Decimal("1")),),
            tariffs=(cez_d25d_2026(),),
        )
