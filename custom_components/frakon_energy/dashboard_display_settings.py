"""Persistent dashboard visibility settings for FRAKON Energy."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping

CONF_DASHBOARD_DISPLAY = "dashboard_display"


@dataclass(frozen=True, slots=True)
class DashboardDisplaySettings:
    """User-controlled visibility of FRAKON Energy dashboard modules.

    All modules default to visible so upgrading an existing installation never
    hides information until the user explicitly opts out.
    """

    show_hdo: bool = True
    show_hdo_plan: bool = True
    show_spot_prices: bool = True
    show_daily_consumption: bool = True
    show_monthly_consumption: bool = True
    show_billing_estimate: bool = True
    show_technical_measurements: bool = True
    show_technology_overview: bool = True
    show_photovoltaics: bool = True
    show_energy_flow: bool = True

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> "DashboardDisplaySettings":
        raw = options.get(CONF_DASHBOARD_DISPLAY, {})
        if raw is None:
            raw = {}
        if not isinstance(raw, Mapping):
            raise ValueError("dashboard display settings must be an object")

        defaults = cls()
        values: dict[str, bool] = {}
        for key, default in asdict(defaults).items():
            value = raw.get(key, default)
            if not isinstance(value, bool):
                raise ValueError(f"dashboard display setting {key} must be boolean")
            values[key] = value
        return cls(**values)

    @classmethod
    def keys(cls) -> tuple[str, ...]:
        return tuple(asdict(cls()).keys())

    def with_value(self, key: str, enabled: bool) -> "DashboardDisplaySettings":
        if key not in self.keys():
            raise ValueError(f"unknown dashboard display setting: {key}")
        if not isinstance(enabled, bool):
            raise ValueError("dashboard display setting value must be boolean")
        return replace(self, **{key: enabled})

    def option_values(self) -> dict[str, dict[str, bool]]:
        return {CONF_DASHBOARD_DISPLAY: self.as_dict()}

    def as_dict(self) -> dict[str, bool]:
        return asdict(self)
