from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class OverviewWidget(StrEnum):
    CURRENT_TARIFF = "current_tariff"
    HDO_TIMELINE = "hdo_timeline"
    CURRENT_PRICE = "current_price"
    DAILY_CONSUMPTION_COST = "daily_consumption_cost"
    MONTHLY_CONSUMPTION_COST = "monthly_consumption_cost"
    BILLING_ESTIMATE = "billing_estimate"
    METER_DIAGNOSTICS = "meter_diagnostics"
    DATA_QUALITY = "data_quality"


class ChartStyle(StrEnum):
    COMBINED = "combined"
    SPLIT = "split"
    STACKED = "stacked"
    TABLE = "table"


class CostView(StrEnum):
    VARIABLE_ONLY = "variable_only"
    TOTAL_WITH_FIXED = "total_with_fixed"


@dataclass(frozen=True, slots=True)
class OverviewWidgetPreference:
    widget: OverviewWidget
    visible: bool = True
    order: int = 0

    def __post_init__(self) -> None:
        if self.order < 0:
            raise ValueError("widget order cannot be negative")


@dataclass(frozen=True, slots=True)
class OverviewChartPreferences:
    daily_style: ChartStyle = ChartStyle.COMBINED
    monthly_style: ChartStyle = ChartStyle.COMBINED
    cost_view: CostView = CostView.VARIABLE_ONLY
    show_vt_nt_split: bool = True
    show_comparison: bool = True


@dataclass(frozen=True, slots=True)
class OverviewPreferences:
    widgets: tuple[OverviewWidgetPreference, ...] = field(default_factory=tuple)
    charts: OverviewChartPreferences = field(default_factory=OverviewChartPreferences)

    def __post_init__(self) -> None:
        widget_ids = [item.widget for item in self.widgets]
        if len(widget_ids) != len(set(widget_ids)):
            raise ValueError("overview widget may be configured only once")
        visible_orders = [item.order for item in self.widgets if item.visible]
        if len(visible_orders) != len(set(visible_orders)):
            raise ValueError("visible overview widgets must have unique order values")

    def visible_widgets(self) -> tuple[OverviewWidgetPreference, ...]:
        return tuple(sorted((item for item in self.widgets if item.visible), key=lambda item: item.order))


def default_overview_preferences() -> OverviewPreferences:
    ordered = (
        OverviewWidget.CURRENT_TARIFF,
        OverviewWidget.HDO_TIMELINE,
        OverviewWidget.DAILY_CONSUMPTION_COST,
        OverviewWidget.MONTHLY_CONSUMPTION_COST,
        OverviewWidget.BILLING_ESTIMATE,
        OverviewWidget.METER_DIAGNOSTICS,
        OverviewWidget.DATA_QUALITY,
        OverviewWidget.CURRENT_PRICE,
    )
    return OverviewPreferences(
        widgets=tuple(
            OverviewWidgetPreference(widget=widget, visible=True, order=index)
            for index, widget in enumerate(ordered)
        )
    )


def overview_payload(preferences: OverviewPreferences) -> dict[str, object]:
    return {
        "widgets": [
            {
                "id": item.widget.value,
                "visible": item.visible,
                "order": item.order,
            }
            for item in preferences.widgets
        ],
        "visible_widgets": [item.widget.value for item in preferences.visible_widgets()],
        "charts": {
            "daily_style": preferences.charts.daily_style.value,
            "monthly_style": preferences.charts.monthly_style.value,
            "cost_view": preferences.charts.cost_view.value,
            "show_vt_nt_split": preferences.charts.show_vt_nt_split,
            "show_comparison": preferences.charts.show_comparison,
        },
    }
