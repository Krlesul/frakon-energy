from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping

from homeassistant.core import HomeAssistant

from .energy_flow_model import build_energy_flow_snapshot
from .energy_flow_settings import CONF_GRID_METER_SCOPE

OPTION_SITE_CAPACITY = "site_capacity"
CONF_MAX_GRID_IMPORT_KW = "max_grid_import_kw"
CONF_EXECUTION_GUARD_ENABLED = "execution_guard_enabled"

STATUS_NOT_CONFIGURED = "not_configured"
STATUS_TOPOLOGY_NOT_READY = "topology_not_ready"
STATUS_SOURCE_UNAVAILABLE = "source_unavailable"
STATUS_WITHIN_LIMIT = "within_limit"
STATUS_OVER_LIMIT = "over_limit"


@dataclass(frozen=True, slots=True)
class SiteCapacitySettings:
    max_grid_import_kw: float | None = None
    execution_guard_enabled: bool = False

    def validated(self) -> "SiteCapacitySettings":
        if self.max_grid_import_kw is not None:
            value = float(self.max_grid_import_kw)
            if not math.isfinite(value) or value <= 0:
                raise ValueError("max_grid_import_kw must be a finite positive number")
        if self.execution_guard_enabled and self.max_grid_import_kw is None:
            raise ValueError("site capacity execution guard requires max_grid_import_kw")
        return self

    def as_dict(self) -> dict[str, float | bool | None]:
        return asdict(self)

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> "SiteCapacitySettings":
        raw = options.get(OPTION_SITE_CAPACITY)
        if not isinstance(raw, Mapping):
            return cls()
        raw_limit = raw.get(CONF_MAX_GRID_IMPORT_KW)
        limit: float | None = None
        if raw_limit not in (None, "") and not isinstance(raw_limit, bool):
            try:
                parsed = float(raw_limit)
            except (TypeError, ValueError):
                parsed = math.nan
            if math.isfinite(parsed) and parsed > 0:
                limit = parsed
        guard = raw.get(CONF_EXECUTION_GUARD_ENABLED) is True
        return cls(max_grid_import_kw=limit, execution_guard_enabled=guard)


def update_site_capacity_limit(
    options: Mapping[str, Any],
    max_grid_import_kw: float | None,
) -> dict[str, Any]:
    """Persist an explicit site import limit while preserving guard intent."""
    existing = SiteCapacitySettings.from_options(options)
    if max_grid_import_kw is None and existing.execution_guard_enabled:
        raise ValueError("disable site capacity execution guard before clearing the limit")
    settings = SiteCapacitySettings(
        max_grid_import_kw=max_grid_import_kw,
        execution_guard_enabled=existing.execution_guard_enabled,
    ).validated()
    updated = dict(options)
    updated[OPTION_SITE_CAPACITY] = settings.as_dict()
    return updated


def update_site_capacity_guard(
    options: Mapping[str, Any],
    enabled: bool,
) -> dict[str, Any]:
    """Explicitly enable/disable the fail-closed capacity execution guard."""
    existing = SiteCapacitySettings.from_options(options)
    settings = SiteCapacitySettings(
        max_grid_import_kw=existing.max_grid_import_kw,
        execution_guard_enabled=bool(enabled),
    ).validated()
    updated = dict(options)
    updated[OPTION_SITE_CAPACITY] = settings.as_dict()
    return updated


@dataclass(frozen=True, slots=True)
class SiteCapacityStatus:
    entry_id: str
    status: str
    configured: bool
    topology_ready: bool
    source_available: bool
    max_grid_import_kw: float | None
    current_grid_import_kw: float | None
    grid_headroom_kw: float | None
    grid_over_limit_kw: float | None
    utilization_percent: float | None
    source_entity_id: str | None
    reason: str
    read_only: bool = True
    service_call_performed: bool = False
    execution_performed: bool = False
    execution_guard_active: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_site_capacity_status(
    hass: HomeAssistant,
    *,
    entry_id: str,
    options: Mapping[str, Any],
) -> SiteCapacityStatus:
    """Evaluate current whole-site grid headroom without authorizing execution."""
    settings = SiteCapacitySettings.from_options(options)
    flow = build_energy_flow_snapshot(hass, entry_id=entry_id, options=options)
    grid = flow.entities["grid_import"]
    limit = settings.max_grid_import_kw
    guard_active = settings.execution_guard_enabled

    if limit is None:
        return SiteCapacityStatus(
            entry_id=entry_id,
            status=STATUS_NOT_CONFIGURED,
            configured=False,
            topology_ready=flow.topology.get(CONF_GRID_METER_SCOPE) == "whole_house",
            source_available=grid.value_kw is not None,
            max_grid_import_kw=None,
            current_grid_import_kw=abs(grid.value_kw) if grid.value_kw is not None else None,
            grid_headroom_kw=None,
            grid_over_limit_kw=None,
            utilization_percent=None,
            source_entity_id=grid.entity_id,
            reason="Maximální odběr ze sítě není nastavený.",
            execution_guard_active=guard_active,
        )

    if flow.topology.get(CONF_GRID_METER_SCOPE) != "whole_house":
        return SiteCapacityStatus(
            entry_id=entry_id,
            status=STATUS_TOPOLOGY_NOT_READY,
            configured=True,
            topology_ready=False,
            source_available=grid.value_kw is not None,
            max_grid_import_kw=limit,
            current_grid_import_kw=abs(grid.value_kw) if grid.value_kw is not None else None,
            grid_headroom_kw=None,
            grid_over_limit_kw=None,
            utilization_percent=None,
            source_entity_id=grid.entity_id,
            reason="Kapacitní ochrana vyžaduje potvrzené hlavní měření celého domu.",
            execution_guard_active=guard_active,
        )

    if grid.value_kw is None:
        return SiteCapacityStatus(
            entry_id=entry_id,
            status=STATUS_SOURCE_UNAVAILABLE,
            configured=True,
            topology_ready=True,
            source_available=False,
            max_grid_import_kw=limit,
            current_grid_import_kw=None,
            grid_headroom_kw=None,
            grid_over_limit_kw=None,
            utilization_percent=None,
            source_entity_id=grid.entity_id,
            reason=f"Aktuální odběr ze sítě není použitelný ({grid.reason}).",
            execution_guard_active=guard_active,
        )

    current = abs(grid.value_kw)
    headroom = max(0.0, limit - current)
    over = max(0.0, current - limit)
    utilization = (current / limit) * 100.0
    status = STATUS_OVER_LIMIT if over > 0 else STATUS_WITHIN_LIMIT
    reason = (
        f"Odběr překračuje nastavený limit o {over:.3f} kW."
        if over > 0
        else f"Do nastaveného limitu zbývá {headroom:.3f} kW."
    )
    return SiteCapacityStatus(
        entry_id=entry_id,
        status=status,
        configured=True,
        topology_ready=True,
        source_available=True,
        max_grid_import_kw=limit,
        current_grid_import_kw=current,
        grid_headroom_kw=headroom,
        grid_over_limit_kw=over,
        utilization_percent=utilization,
        source_entity_id=grid.entity_id,
        reason=reason,
        execution_guard_active=guard_active,
    )
