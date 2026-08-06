import pytest

from custom_components.frakon_energy.overview import (
    ChartStyle,
    CostView,
    OverviewChartPreferences,
    OverviewPreferences,
    OverviewWidget,
    OverviewWidgetPreference,
    default_overview_preferences,
    overview_payload,
)


def test_default_overview_contains_daily_and_monthly_cost_charts() -> None:
    payload = overview_payload(default_overview_preferences())

    assert "daily_consumption_cost" in payload["visible_widgets"]
    assert "monthly_consumption_cost" in payload["visible_widgets"]
    assert payload["charts"]["cost_view"] == "variable_only"
    assert payload["charts"]["show_vt_nt_split"] is True


def test_overview_can_hide_and_reorder_widgets() -> None:
    preferences = OverviewPreferences(
        widgets=(
            OverviewWidgetPreference(OverviewWidget.HDO_TIMELINE, visible=False, order=3),
            OverviewWidgetPreference(OverviewWidget.MONTHLY_CONSUMPTION_COST, order=1),
            OverviewWidgetPreference(OverviewWidget.DAILY_CONSUMPTION_COST, order=0),
        ),
        charts=OverviewChartPreferences(
            daily_style=ChartStyle.SPLIT,
            monthly_style=ChartStyle.TABLE,
            cost_view=CostView.TOTAL_WITH_FIXED,
            show_vt_nt_split=False,
        ),
    )

    payload = overview_payload(preferences)
    assert payload["visible_widgets"] == [
        "daily_consumption_cost",
        "monthly_consumption_cost",
    ]
    assert payload["charts"]["daily_style"] == "split"
    assert payload["charts"]["monthly_style"] == "table"
    assert payload["charts"]["cost_view"] == "total_with_fixed"


def test_overview_rejects_duplicate_widgets() -> None:
    with pytest.raises(ValueError, match="only once"):
        OverviewPreferences(
            widgets=(
                OverviewWidgetPreference(OverviewWidget.CURRENT_TARIFF, order=0),
                OverviewWidgetPreference(OverviewWidget.CURRENT_TARIFF, order=1),
            )
        )


def test_overview_rejects_duplicate_visible_order() -> None:
    with pytest.raises(ValueError, match="unique order"):
        OverviewPreferences(
            widgets=(
                OverviewWidgetPreference(OverviewWidget.CURRENT_TARIFF, order=0),
                OverviewWidgetPreference(OverviewWidget.HDO_TIMELINE, order=0),
            )
        )
