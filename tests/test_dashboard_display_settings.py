from __future__ import annotations

import pytest

from custom_components.frakon_energy.dashboard_display_settings import (
    CONF_DASHBOARD_DISPLAY,
    DashboardDisplaySettings,
)


def test_dashboard_display_settings_default_to_visible() -> None:
    settings = DashboardDisplaySettings.from_options({})

    assert settings.as_dict()
    assert all(settings.as_dict().values())
    assert "show_hdo" in settings.keys()
    assert "show_spot_prices" in settings.keys()
    assert "show_daily_consumption" in settings.keys()
    assert "show_photovoltaics" in settings.keys()
    assert "show_energy_flow" in settings.keys()


def test_dashboard_display_settings_preserve_partial_saved_values() -> None:
    settings = DashboardDisplaySettings.from_options(
        {
            "unrelated": "keep-me",
            CONF_DASHBOARD_DISPLAY: {
                "show_hdo": False,
                "show_spot_prices": False,
                "show_photovoltaics": False,
            },
        }
    )

    assert settings.show_hdo is False
    assert settings.show_spot_prices is False
    assert settings.show_photovoltaics is False
    assert settings.show_daily_consumption is True
    assert settings.show_energy_flow is True


def test_dashboard_display_settings_update_one_value_without_resetting_others() -> None:
    original = DashboardDisplaySettings(
        show_hdo=False,
        show_spot_prices=False,
        show_daily_consumption=False,
    )

    updated = original.with_value("show_photovoltaics", False)

    assert updated.show_photovoltaics is False
    assert updated.show_hdo is False
    assert updated.show_spot_prices is False
    assert updated.show_daily_consumption is False
    assert updated.show_hdo_plan is True


def test_dashboard_display_settings_serialize_as_single_options_namespace() -> None:
    settings = DashboardDisplaySettings(show_hdo=False, show_energy_flow=False)

    serialized = settings.option_values()

    assert set(serialized) == {CONF_DASHBOARD_DISPLAY}
    assert serialized[CONF_DASHBOARD_DISPLAY]["show_hdo"] is False
    assert serialized[CONF_DASHBOARD_DISPLAY]["show_energy_flow"] is False
    assert serialized[CONF_DASHBOARD_DISPLAY]["show_spot_prices"] is True


@pytest.mark.parametrize("bad_value", ["false", 0, 1, None, [], {}])
def test_dashboard_display_settings_fail_closed_on_non_boolean_value(bad_value: object) -> None:
    with pytest.raises(ValueError, match="must be boolean"):
        DashboardDisplaySettings.from_options(
            {CONF_DASHBOARD_DISPLAY: {"show_hdo": bad_value}}
        )


def test_dashboard_display_settings_reject_unknown_key() -> None:
    with pytest.raises(ValueError, match="unknown dashboard display setting"):
        DashboardDisplaySettings().with_value("show_magic", False)
