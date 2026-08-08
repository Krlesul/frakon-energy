from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
from typing import Any, Mapping

from homeassistant.core import HomeAssistant

from .energy_flow_model import build_energy_flow_snapshot
from .energy_flow_settings import CONF_GRID_METER_SCOPE

OPTION_SITE_CAPACITY = "site_capacity"
CONF_MAX_GRID_IMPORT_KW = "max_grid_import_kw"
CONF_EXECUTION_GUARD_ENABLED = "execution_guard_enabled"
DEFAULT_CAPACITY_SOURCE_MAX_AGE_SECONDS = 300

STATUS_NOT_CONFIGURED = "not_configured"
STATUS_TOPOLOGY_NOT_READY = "topology_not_ready"
STATUS_SOURCE_UNAVAILABLE = "source_unavailable"
STATUS_SOURCE_STALE = "source_stale"
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
        if not isinstance(self.execution_guard_enabled, bool):
            raise ValueError("execution_guard_enabled must be boolean")
        if self.execution_guard_enabled and self.max_grid_import_kw is None:
            raise ValueError("execution_guard_enabled requires max_grid_import_kw")
        return self

    def as_dict(self) -> dict[str, float | bool | None]:
        return asdict(self)

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> "SiteCapacitySettings":
        raw = options.get(OPTION_SITE_CAPACITY)
        if not isinstance(raw, Mapping):
            return cls()
        value = raw.get(CONF_MAX_GRID_IMPORT_KW)
        if value in (None, "") or isinstance(value, bool):
            return cls()
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return cls()
        if not math.isfinite(parsed) or parsed <= 0:
            return cls()

        raw_guard = raw.get(CONF_EXECUTION_GUARD_ENABLED)
        if isinstance(raw_guard, bool):
            guard_enabled = raw_guard
        else:
            # Before the explicit flag existed, every configured limit already
            # enforced execution. Preserve that protection for existing installs.
            guard_enabled = True
        return cls(
            max_grid_import_kw=parsed,
            execution_guard_enabled=guard_enabled,
        )


def update_site_capacity_settings(
    options: Mapping[str, Any],
    *,
    max_grid_import_kw: float | None,
    execution_guard_enabled: bool,
) -> dict[str, Any]:
    """Persist explicit capacity diagnostics/enforcement while preserving other options."""
    settings = SiteCapacitySettings(
        max_grid_import_kw=max_grid_import_kw,
        execution_guard_enabled=execution_guard_enabled,
    ).validated()
    updated = dict(options)
    updated[OPTION_SITE_CAPACITY] = settings.as_dict()
    return updated


def update_site_capacity_limit(
    options: Mapping[str, Any],
    max_grid_import_kw: float | None,
) -> dict[str, Any]:
    """Compatibility helper preserving the current explicit/legacy guard state."""
    current = SiteCapacitySettings.from_options(options)
    guard_enabled = current.execution_guard_enabled if max_grid_import_kw is not None else False
    return update_site_capacity_settings(
        options,
        max_grid_import_kw=max_grid_import_kw,
        execution_guard_enabled=guard_enabled,
    )


@dataclass(frozen=True, slots=True)
class SiteCapacityStatus:
    entry_id: str
    status: str
    configured: bool
    topology_ready: bool
    source_available: bool
    source_fresh: bool
    source_age_seconds: float | None
    max_source_age_seconds: int
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


def _aware_now(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return current


def _source_freshness(
    hass: HomeAssistant,
    *,
    entity_id: str | None,
    now: datetime,
) -> tuple[bool, float | None]:
    if not entity_id:
        return False, None
    state = hass.states.get(entity_id)
    if state is None:
        return False, None
    last_updated = getattr(state, "last_updated", None)
    if not isinstance(last_updated, datetime):
        return False, None
    if last_updated.tzinfo is None or last_updated.utcoffset() is None:
        return False, None
    age = max(0.0, (now - last_updated).total_seconds())
    return age <= DEFAULT_CAPACITY_SOURCE_MAX_AGE_SECONDS, age


def build_site_capacity_status(
    hass: HomeAssistant,
    *,
    entry_id: str,
    options: Mapping[str, Any],
    now: datetime | None = None,
) -> SiteCapacityStatus:
    """Evaluate current whole-site grid headroom without authorizing execution."""
    current_time = _aware_now(now)
    settings = SiteCapacitySettings.from_options(options)
    flow = build_energy_flow_snapshot(hass, entry_id=entry_id, options=options)
    grid = flow.entities["grid_import"]
    limit = settings.max_grid_import_kw
    guard_active = bool(limit is not None and settings.execution_guard_enabled)
    source_fresh, source_age = _source_freshness(
        hass,
        entity_id=grid.entity_id,
        now=current_time,
    )

    common = dict(
        entry_id=entry_id,
        source_fresh=source_fresh,
        source_age_seconds=source_age,
        max_source_age_seconds=DEFAULT_CAPACITY_SOURCE_MAX_AGE_SECONDS,
        source_entity_id=grid.entity_id,
        execution_guard_active=guard_active,
    )

    if limit is None:
        return SiteCapacityStatus(
            status=STATUS_NOT_CONFIGURED,
            configured=False,
            topology_ready=flow.topology.get(CONF_GRID_METER_SCOPE) == "whole_house",
            source_available=grid.value_kw is not None,
            max_grid_import_kw=None,
            current_grid_import_kw=abs(grid.value_kw) if grid.value_kw is not None else None,
            grid_headroom_kw=None,
            grid_over_limit_kw=None,
            utilization_percent=None,
            reason="Maximální odběr ze sítě není nastavený.",
            **common,
        )

    if flow.topology.get(CONF_GRID_METER_SCOPE) != "whole_house":
        return SiteCapacityStatus(
            status=STATUS_TOPOLOGY_NOT_READY,
            configured=True,
            topology_ready=False,
            source_available=grid.value_kw is not None,
            max_grid_import_kw=limit,
            current_grid_import_kw=abs(grid.value_kw) if grid.value_kw is not None else None,
            grid_headroom_kw=None,
            grid_over_limit_kw=None,
            utilization_percent=None,
            reason="Kapacitní ochrana vyžaduje potvrzené hlavní měření celého domu.",
            **common,
        )

    if grid.value_kw is None:
        return SiteCapacityStatus(
            status=STATUS_SOURCE_UNAVAILABLE,
            configured=True,
            topology_ready=True,
            source_available=False,
            max_grid_import_kw=limit,
            current_grid_import_kw=None,
            grid_headroom_kw=None,
            grid_over_limit_kw=None,
            utilization_percent=None,
            reason=f"Aktuální odběr ze sítě není použitelný ({grid.reason}).",
            **common,
        )

    if not source_fresh:
        age_label = f"{source_age:.1f} s" if source_age is not None else "neznámé"
        return SiteCapacityStatus(
            status=STATUS_SOURCE_STALE,
            configured=True,
            topology_ready=True,
            source_available=True,
            max_grid_import_kw=limit,
            current_grid_import_kw=abs(grid.value_kw),
            grid_headroom_kw=None,
            grid_over_limit_kw=None,
            utilization_percent=None,
            reason=(
                "Měření odběru ze sítě je příliš staré pro bezpečné řízení "
                f"(stáří {age_label}, maximum {DEFAULT_CAPACITY_SOURCE_MAX_AGE_SECONDS} s)."
            ),
            **common,
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
        status=status,
        configured=True,
        topology_ready=True,
        source_available=True,
        max_grid_import_kw=limit,
        current_grid_import_kw=current,
        grid_headroom_kw=headroom,
        grid_over_limit_kw=over,
        utilization_percent=utilization,
        reason=reason,
        **common,
    )