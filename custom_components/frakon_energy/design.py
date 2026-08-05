from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final


class AppearanceMode(StrEnum):
    SYSTEM = "system"
    DARK = "dark"
    LIGHT = "light"


class AccentFamily(StrEnum):
    GOLD = "gold"
    EMERALD = "emerald"
    SAPPHIRE = "sapphire"
    CRIMSON = "crimson"
    VIOLET = "violet"
    ARCTIC = "arctic"
    ORANGE = "orange"
    GRAPHITE = "graphite"


class AccentIntensity(StrEnum):
    SOFT = "soft"
    NORMAL = "normal"
    STRONG = "strong"


class Density(StrEnum):
    COMPACT = "compact"
    COMFORTABLE = "comfortable"
    TOUCH = "touch"


class MotionLevel(StrEnum):
    OFF = "off"
    MINIMAL = "minimal"
    NORMAL = "normal"
    RICH = "rich"


class GlassLevel(StrEnum):
    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Breakpoint(StrEnum):
    DESKTOP = "desktop"
    TABLET = "tablet"
    MOBILE = "mobile"


GRID_COLUMNS: Final[dict[Breakpoint, int]] = {
    Breakpoint.DESKTOP: 12,
    Breakpoint.TABLET: 8,
    Breakpoint.MOBILE: 4,
}


@dataclass(frozen=True, slots=True)
class ThemePreferences:
    appearance: AppearanceMode = AppearanceMode.SYSTEM
    accent: AccentFamily = AccentFamily.GOLD
    intensity: AccentIntensity = AccentIntensity.NORMAL
    density: Density = Density.COMFORTABLE
    motion: MotionLevel = MotionLevel.NORMAL
    glass: GlassLevel = GlassLevel.LOW
    corner_radius_px: int = 22
    shadow_strength: int = 35

    def __post_init__(self) -> None:
        if not 0 <= self.corner_radius_px <= 40:
            raise ValueError("corner_radius_px must be between 0 and 40")
        if not 0 <= self.shadow_strength <= 100:
            raise ValueError("shadow_strength must be between 0 and 100")


@dataclass(frozen=True, slots=True)
class WidgetPlacement:
    widget_id: str
    breakpoint: Breakpoint
    x: int
    y: int
    width: int
    height: int
    min_width: int = 1
    min_height: int = 1
    max_width: int | None = None
    max_height: int | None = None

    def __post_init__(self) -> None:
        if not self.widget_id.strip():
            raise ValueError("widget_id is required")
        columns = GRID_COLUMNS[self.breakpoint]
        if self.x < 0 or self.y < 0:
            raise ValueError("widget coordinates cannot be negative")
        if self.width < 1 or self.height < 1:
            raise ValueError("widget dimensions must be positive")
        if self.x + self.width > columns:
            raise ValueError("widget exceeds breakpoint grid width")
        if self.min_width < 1 or self.min_height < 1:
            raise ValueError("minimum dimensions must be positive")
        if self.width < self.min_width or self.height < self.min_height:
            raise ValueError("widget is smaller than its minimum dimensions")
        if self.max_width is not None and self.width > self.max_width:
            raise ValueError("widget exceeds max_width")
        if self.max_height is not None and self.height > self.max_height:
            raise ValueError("widget exceeds max_height")

    def overlaps(self, other: WidgetPlacement) -> bool:
        if self.breakpoint != other.breakpoint:
            return False
        return not (
            self.x + self.width <= other.x
            or other.x + other.width <= self.x
            or self.y + self.height <= other.y
            or other.y + other.height <= self.y
        )


@dataclass(frozen=True, slots=True)
class DashboardLayout:
    name: str = "Výchozí"
    placements: tuple[WidgetPlacement, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("layout name is required")
        seen: set[tuple[str, Breakpoint]] = set()
        for placement in self.placements:
            key = (placement.widget_id, placement.breakpoint)
            if key in seen:
                raise ValueError("widget may appear only once per breakpoint")
            seen.add(key)
        for index, placement in enumerate(self.placements):
            for other in self.placements[index + 1 :]:
                if placement.overlaps(other):
                    raise ValueError(
                        f"widgets {placement.widget_id!r} and {other.widget_id!r} overlap"
                    )


@dataclass(frozen=True, slots=True)
class DesignStudioPreferences:
    theme: ThemePreferences = field(default_factory=ThemePreferences)
    active_layout: str = "Výchozí"
    layouts: tuple[DashboardLayout, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        names = [layout.name for layout in self.layouts]
        if len(names) != len(set(names)):
            raise ValueError("layout names must be unique")
        if self.layouts and self.active_layout not in names:
            raise ValueError("active_layout must reference a saved layout")


def default_design_preferences() -> DesignStudioPreferences:
    placements = (
        WidgetPlacement("hero", Breakpoint.DESKTOP, 0, 0, 12, 2, min_height=2),
        WidgetPlacement("hdo_timeline", Breakpoint.DESKTOP, 0, 2, 12, 3, min_height=2),
        WidgetPlacement("billing", Breakpoint.DESKTOP, 0, 5, 6, 3),
        WidgetPlacement("consumption", Breakpoint.DESKTOP, 6, 5, 6, 3),
        WidgetPlacement("hero", Breakpoint.TABLET, 0, 0, 8, 2, min_height=2),
        WidgetPlacement("hdo_timeline", Breakpoint.TABLET, 0, 2, 8, 3, min_height=2),
        WidgetPlacement("billing", Breakpoint.TABLET, 0, 5, 4, 3),
        WidgetPlacement("consumption", Breakpoint.TABLET, 4, 5, 4, 3),
        WidgetPlacement("hero", Breakpoint.MOBILE, 0, 0, 4, 2, min_height=2),
        WidgetPlacement("hdo_timeline", Breakpoint.MOBILE, 0, 2, 4, 3, min_height=2),
        WidgetPlacement("billing", Breakpoint.MOBILE, 0, 5, 4, 3),
        WidgetPlacement("consumption", Breakpoint.MOBILE, 0, 8, 4, 3),
    )
    layout = DashboardLayout(name="Výchozí", placements=placements)
    return DesignStudioPreferences(layouts=(layout,))


def design_payload(preferences: DesignStudioPreferences) -> dict[str, object]:
    return {
        "theme": {
            "appearance": preferences.theme.appearance.value,
            "accent": preferences.theme.accent.value,
            "intensity": preferences.theme.intensity.value,
            "density": preferences.theme.density.value,
            "motion": preferences.theme.motion.value,
            "glass": preferences.theme.glass.value,
            "corner_radius_px": preferences.theme.corner_radius_px,
            "shadow_strength": preferences.theme.shadow_strength,
        },
        "active_layout": preferences.active_layout,
        "layouts": [
            {
                "name": layout.name,
                "placements": [
                    {
                        "widget_id": placement.widget_id,
                        "breakpoint": placement.breakpoint.value,
                        "x": placement.x,
                        "y": placement.y,
                        "width": placement.width,
                        "height": placement.height,
                        "min_width": placement.min_width,
                        "min_height": placement.min_height,
                        "max_width": placement.max_width,
                        "max_height": placement.max_height,
                    }
                    for placement in layout.placements
                ],
            }
            for layout in preferences.layouts
        ],
    }
