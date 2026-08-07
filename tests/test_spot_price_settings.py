import pytest

from custom_components.frakon_energy.spot_price_settings import (
    CONF_SPOT_EUR_CZK,
    CONF_SPOT_SUPPLIER_FEE,
    SpotPriceSettings,
)


def test_settings_load_defaults() -> None:
    settings = SpotPriceSettings.from_options({})
    assert settings.eur_czk == 25.0
    assert settings.vat_percent == 21.0


def test_settings_load_saved_options() -> None:
    settings = SpotPriceSettings.from_options({
        CONF_SPOT_EUR_CZK: 24.7,
        CONF_SPOT_SUPPLIER_FEE: 0.35,
    })
    assert settings.eur_czk == 24.7
    assert settings.supplier_fee_czk_kwh == 0.35


def test_invalid_exchange_rate_is_rejected() -> None:
    with pytest.raises(ValueError):
        SpotPriceSettings(eur_czk=5.0).validated()


def test_option_values_round_trip() -> None:
    original = SpotPriceSettings(eur_czk=24.9, supplier_fee_czk_kwh=0.2, variable_additions_czk_kwh=1.1, vat_percent=21.0)
    restored = SpotPriceSettings.from_options(original.option_values())
    assert restored == original
