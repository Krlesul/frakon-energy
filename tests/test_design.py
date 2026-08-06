from __future__ import annotations

import pytest

from custom_components.frakon_energy.design import (
    AccentFamily,
    Breakpoint,
    DashboardLayout,
    DesignStudioPreferences,
    ThemePreferences,
    WidgetPlacement,
    default_design_preferences,
    design_payload,
)


def test_default_design_contains_responsive_layouts() -> None:
    preferences = default_design_preferences()
    assert preferences.active_layout == "Výchozí"
    layout = preferences.layouts[0]
    breakpoints = {placement.breakpoint for placement in layout.placements}
    assert breakpoints == {Breakpoint.DESKTOP, Breakpoint.TABLET, Breakpoint.MOBILE}
    assert design_payload(preferences)["theme"]["accent"] == AccentFamily.GOLD.value


def test_widget_cannot_exceed_breakpoint_grid() -> None:
    with pytest.raises(ValueError, match="exceeds breakpoint grid width"):
        WidgetPlacement("bad", Breakpoint.MOBILE, 2, 0, 3, 1)


def test_layout_rejects_overlap() -> None:
    first = WidgetPlacement("first", Breakpoint.DESKTOP, 0, 0, 6, 2)
    second = WidgetPlacement("second", Breakpoint.DESKTOP, 5, 1, 4, 2)
    with pytest.raises(ValueError, match="overlap"):
        DashboardLayout("Invalid", (first, second))


def test_same_positions_are_allowed_on_different_breakpoints() -> None:
    desktop = WidgetPlacement("hero", Breakpoint.DESKTOP, 0, 0, 12, 2)
    mobile = WidgetPlacement("hero", Breakpoint.MOBILE, 0, 0, 4, 2)
    layout = DashboardLayout("Responsive", (desktop, mobile))
    assert len(layout.placements) == 2


def test_theme_rejects_invalid_ranges() -> None:
    with pytest.raises(ValueError, match="corner_radius_px"):
        ThemePreferences(corner_radius_px=41)
    with pytest.raises(ValueError, match="shadow_strength"):
        ThemePreferences(shadow_strength=-1)


def test_active_layout_must_exist() -> None:
    layout = DashboardLayout("A", ())
    with pytest.raises(ValueError, match="active_layout"):
        DesignStudioPreferences(active_layout="B", layouts=(layout,))
