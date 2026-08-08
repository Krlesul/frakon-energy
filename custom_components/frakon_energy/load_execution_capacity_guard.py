from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .site_capacity import (
    STATUS_OVER_LIMIT,
    STATUS_SOURCE_UNAVAILABLE,
    STATUS_TOPOLOGY_NOT_READY,
    STATUS_WITHIN_LIMIT,
    SiteCapacitySettings,
    build_site_capacity_status,
)

GUARD_DISABLED = "disabled"
GUARD_READY = "ready"
GUARD_BLOCKED = "blocked"

REASON_DISABLED = "capacity_guard_disabled"
REASON_READY = "capacity_ready"
REASON_LIMIT_MISSING = "capacity_limit_missing"
REASON_TOPOLOGY_NOT_READY = "capacity_topology_not_ready"
REASON_SOURCE_UNAVAILABLE = "capacity_source_unavailable"
REASON_ALREADY_OVER_LIMIT = "capacity_already_over_limit"
REASON_INSUFFICIENT_HEADROOM = "capacity_insufficient_headroom"
REASON_PLAN_POWER_INVALID = "capacity_plan_power_invalid"


@dataclass(frozen=True, slots=True)
class SiteCapacityStartDecision:
    status: str
    reason: str
    entry_id: str
    guard_enabled: bool
    guard_applies: bool
    additional_power_kw: float
    max_grid_import_kw: float | None
    current_grid_import_kw: float | None
    projected_grid_import_kw: float | None
    headroom_before_kw: float | None
    headroom_after_kw: float | None
    projected_over_limit_kw: float | None
    source_entity_id: str | None
    can_start: bool
    read_only: bool = True
    service_call_performed: bool = False
    execution_performed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _entry(hass: HomeAssistant, entry_id: str):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ValueError("FRAKON Energy config entry not found")
    return entry


def evaluate_site_capacity_start(
    hass: HomeAssistant,
    *,
    entry_id: str,
    additional_power_kw: float,
) -> SiteCapacityStartDecision:
    """Evaluate whether one additional bounded load fits the configured site limit."""
    try:
        power = float(additional_power_kw)
    except (TypeError, ValueError) as err:
        raise ValueError("additional_power_kw must be numeric") from err
    if not math.isfinite(power) or power <= 0:
        return SiteCapacityStartDecision(
            status=GUARD_BLOCKED,
            reason=REASON_PLAN_POWER_INVALID,
            entry_id=entry_id,
            guard_enabled=True,
            guard_applies=True,
            additional_power_kw=power,
            max_grid_import_kw=None,
            current_grid_import_kw=None,
            projected_grid_import_kw=None,
            headroom_before_kw=None,
            headroom_after_kw=None,
            projected_over_limit_kw=None,
            source_entity_id=None,
            can_start=False,
        )

    entry = _entry(hass, entry_id)
    settings = SiteCapacitySettings.from_options(entry.options)
    capacity = build_site_capacity_status(
        hass,
        entry_id=entry_id,
        options=entry.options,
    )

    if not settings.execution_guard_enabled:
        return SiteCapacityStartDecision(
            status=GUARD_DISABLED,
            reason=REASON_DISABLED,
            entry_id=entry_id,
            guard_enabled=False,
            guard_applies=False,
            additional_power_kw=power,
            max_grid_import_kw=capacity.max_grid_import_kw,
            current_grid_import_kw=capacity.current_grid_import_kw,
            projected_grid_import_kw=(
                capacity.current_grid_import_kw + power
                if capacity.current_grid_import_kw is not None
                else None
            ),
            headroom_before_kw=capacity.grid_headroom_kw,
            headroom_after_kw=None,
            projected_over_limit_kw=None,
            source_entity_id=capacity.source_entity_id,
            can_start=True,
        )

    limit = capacity.max_grid_import_kw
    if limit is None:
        reason = REASON_LIMIT_MISSING
    elif capacity.status == STATUS_TOPOLOGY_NOT_READY:
        reason = REASON_TOPOLOGY_NOT_READY
    elif capacity.status == STATUS_SOURCE_UNAVAILABLE:
        reason = REASON_SOURCE_UNAVAILABLE
    elif capacity.status == STATUS_OVER_LIMIT:
        reason = REASON_ALREADY_OVER_LIMIT
    elif capacity.status != STATUS_WITHIN_LIMIT or capacity.current_grid_import_kw is None:
        reason = REASON_SOURCE_UNAVAILABLE
    else:
        projected = capacity.current_grid_import_kw + power
        projected_over = max(0.0, projected - limit)
        if projected_over > 1e-9:
            return SiteCapacityStartDecision(
                status=GUARD_BLOCKED,
                reason=REASON_INSUFFICIENT_HEADROOM,
                entry_id=entry_id,
                guard_enabled=True,
                guard_applies=True,
                additional_power_kw=power,
                max_grid_import_kw=limit,
                current_grid_import_kw=capacity.current_grid_import_kw,
                projected_grid_import_kw=projected,
                headroom_before_kw=capacity.grid_headroom_kw,
                headroom_after_kw=0.0,
                projected_over_limit_kw=projected_over,
                source_entity_id=capacity.source_entity_id,
                can_start=False,
            )
        return SiteCapacityStartDecision(
            status=GUARD_READY,
            reason=REASON_READY,
            entry_id=entry_id,
            guard_enabled=True,
            guard_applies=True,
            additional_power_kw=power,
            max_grid_import_kw=limit,
            current_grid_import_kw=capacity.current_grid_import_kw,
            projected_grid_import_kw=projected,
            headroom_before_kw=capacity.grid_headroom_kw,
            headroom_after_kw=max(0.0, limit - projected),
            projected_over_limit_kw=0.0,
            source_entity_id=capacity.source_entity_id,
            can_start=True,
        )

    return SiteCapacityStartDecision(
        status=GUARD_BLOCKED,
        reason=reason,
        entry_id=entry_id,
        guard_enabled=True,
        guard_applies=True,
        additional_power_kw=power,
        max_grid_import_kw=limit,
        current_grid_import_kw=capacity.current_grid_import_kw,
        projected_grid_import_kw=(
            capacity.current_grid_import_kw + power
            if capacity.current_grid_import_kw is not None
            else None
        ),
        headroom_before_kw=capacity.grid_headroom_kw,
        headroom_after_kw=None,
        projected_over_limit_kw=None,
        source_entity_id=capacity.source_entity_id,
        can_start=False,
    )
