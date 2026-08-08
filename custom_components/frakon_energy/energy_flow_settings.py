from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CONF_ENERGY_FLOW = "energy_flow"
CONF_BATTERY_POWER_SIGN = "battery_power_sign"
CONF_GRID_METER_SCOPE = "grid_meter_scope"
CONF_PV_POWER_SCOPE = "pv_power_scope"
CONF_EV_WALLBOX_RELATION = "ev_wallbox_relation"

BATTERY_POWER_SIGNS = ("unknown", "positive_is_charge", "positive_is_discharge")
GRID_METER_SCOPES = ("unknown", "whole_house", "inverter_branch")
PV_POWER_SCOPES = ("unknown", "gross_generation", "inverter_net")
EV_WALLBOX_RELATIONS = ("unknown", "same_flow", "separate")


def _validated_setting(
    stored: Mapping[str, Any],
    key: str,
    allowed: tuple[str, ...],
    default: str = "unknown",
) -> str:
    value = str(stored.get(key, default))
    return value if value in allowed else default


def flow_settings_from_options(options: Mapping[str, Any]) -> dict[str, str]:
    """Return validated fail-closed energy-flow topology settings."""
    raw = options.get(CONF_ENERGY_FLOW, {})
    stored = raw if isinstance(raw, Mapping) else {}
    return {
        CONF_BATTERY_POWER_SIGN: _validated_setting(
            stored, CONF_BATTERY_POWER_SIGN, BATTERY_POWER_SIGNS
        ),
        CONF_GRID_METER_SCOPE: _validated_setting(
            stored, CONF_GRID_METER_SCOPE, GRID_METER_SCOPES
        ),
        CONF_PV_POWER_SCOPE: _validated_setting(
            stored, CONF_PV_POWER_SCOPE, PV_POWER_SCOPES
        ),
        CONF_EV_WALLBOX_RELATION: _validated_setting(
            stored, CONF_EV_WALLBOX_RELATION, EV_WALLBOX_RELATIONS
        ),
    }
