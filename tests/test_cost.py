from datetime import date
from decimal import Decimal

import pytest

from custom_components.frakon_energy.cost import (
    FixedPriceComponent,
    FixedPriceComponentKind,
    TariffPriceBreakdown,
    TariffPrices,
    VariablePriceComponent,
    VariablePriceComponentKind,
    calculate_cost_projection,
)


def _component(
    kind: VariablePriceComponentKind,
    high: str,
    low: str,
) -> VariablePriceComponent:
    return VariablePriceComponent(
        kind=kind,
        high_rate_czk_per_kwh=Decimal(high),
        low_rate_czk_per_kwh=Decimal(low),
    )


def test_breakdown_sums_variable_rates_and_keeps_fixed_charges_separate() -> None:
    breakdown = TariffPriceBreakdown(
        variable_components=(
            _component(VariablePriceComponentKind.COMMODITY, "2.00", "2.00"),
            _component(VariablePriceComponentKind.DISTRIBUTION, "3.00", "1.00"),
            _component(VariablePriceComponentKind.POZE, "0.50", "0.50"),
            _component(VariablePriceComponentKind.SYSTEM_SERVICES, "0.20", "0.20"),
            _component(VariablePriceComponentKind.ELECTRICITY_TAX, "0.10", "0.10"),
            _component(VariablePriceComponentKind.MARKET, "0.05", "0.05"),
        ),
        fixed_components=(
            FixedPriceComponent(
                kind=FixedPriceComponentKind.SUPPLIER_STANDING,
                monthly_czk=Decimal("120"),
            ),
            FixedPriceComponent(
                kind=FixedPriceComponentKind.BREAKER,
                monthly_czk=Decimal("80"),
            ),
        ),
    )

    assert breakdown.all_in_vt_czk_kwh == Decimal("5.85")
    assert breakdown.all_in_nt_czk_kwh == Decimal("3.85")
    assert breakdown.fixed_monthly_total_czk == Decimal("200")
    assert breakdown.to_tariff_prices() == TariffPrices(
        high_rate_czk_per_kwh=Decimal("5.85"),
        low_rate_czk_per_kwh=Decimal("3.85"),
        fixed_monthly_czk=Decimal("200"),
    )


def test_net_components_are_normalized_to_gross_before_all_in_totals() -> None:
    breakdown = TariffPriceBreakdown(
        variable_components=(
            VariablePriceComponent(
                kind=VariablePriceComponentKind.COMMODITY,
                high_rate_czk_per_kwh=Decimal("1.00"),
                low_rate_czk_per_kwh=Decimal("0.50"),
                vat_included=False,
                vat_rate_percent=Decimal("21"),
            ),
            _component(VariablePriceComponentKind.DISTRIBUTION, "0.79", "0.395"),
        ),
        fixed_components=(
            FixedPriceComponent(
                kind=FixedPriceComponentKind.SUPPLIER_STANDING,
                monthly_czk=Decimal("100"),
                vat_included=False,
                vat_rate_percent=Decimal("21"),
            ),
        ),
    )

    assert breakdown.all_in_vt_czk_kwh == Decimal("2.0000")
    assert breakdown.all_in_nt_czk_kwh == Decimal("1.000")
    assert breakdown.fixed_monthly_total_czk == Decimal("121")


def test_component_validation_rejects_negative_non_finite_and_empty_breakdown() -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        VariablePriceComponent(
            kind=VariablePriceComponentKind.COMMODITY,
            high_rate_czk_per_kwh=Decimal("-0.01"),
            low_rate_czk_per_kwh=Decimal("1"),
        )

    with pytest.raises(ValueError, match="finite and non-negative"):
        FixedPriceComponent(
            kind=FixedPriceComponentKind.BREAKER,
            monthly_czk=Decimal("Infinity"),
        )

    with pytest.raises(ValueError, match="at least one variable"):
        TariffPriceBreakdown(variable_components=())


def test_component_breakdown_feeds_existing_cost_projection_without_fixed_rate_smearing() -> None:
    breakdown = TariffPriceBreakdown(
        variable_components=(
            _component(VariablePriceComponentKind.COMMODITY, "2", "1"),
        ),
        fixed_components=(),
    )

    result = calculate_cost_projection(
        cycle_start=date(2026, 1, 1),
        settlement_date=date(2026, 1, 1),
        as_of=date(2026, 1, 1),
        baseline_high_rate_kwh=Decimal("0"),
        baseline_low_rate_kwh=Decimal("0"),
        current_high_rate_kwh=Decimal("1"),
        current_low_rate_kwh=Decimal("1"),
        prices=breakdown.to_tariff_prices(),
    )

    assert result.accrued_energy_cost_czk == Decimal("3.00")
    assert result.accrued_fixed_cost_czk == Decimal("0.00")
    assert result.accrued_total_cost_czk == Decimal("3.00")
    assert result.projected_total_cost_czk == Decimal("3.00")
