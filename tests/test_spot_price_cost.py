import pytest

from custom_components.frakon_energy.spot_price_cost import (
    SpotPriceCostConfig,
    calculate_spot_cost,
)


def test_calculate_spot_cost_converts_and_adds_vat() -> None:
    result = calculate_spot_cost(
        100.0,
        SpotPriceCostConfig(
            eur_czk=25.0,
            supplier_fee_czk_kwh=0.25,
            variable_additions_czk_kwh=1.0,
            vat_percent=21.0,
        ),
    )

    assert result["wholesale_czk_kwh"] == pytest.approx(2.5)
    assert result["vat_czk_kwh"] == pytest.approx(0.7875)
    assert result["total_czk_kwh"] == pytest.approx(4.5375)


def test_negative_spot_price_is_preserved() -> None:
    result = calculate_spot_cost(
        -20.0,
        SpotPriceCostConfig(eur_czk=25.0, vat_percent=0.0),
    )

    assert result["wholesale_czk_kwh"] == pytest.approx(-0.5)
    assert result["total_czk_kwh"] == pytest.approx(-0.5)


def test_exchange_rate_must_be_positive() -> None:
    with pytest.raises(ValueError):
        SpotPriceCostConfig(eur_czk=0.0)
