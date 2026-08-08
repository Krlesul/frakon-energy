"""Read-only per-phase current capacity diagnostics for FRAKON Energy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import math
from typing import Any, Mapping

from homeassistant.core import HomeAssistant

from .site_phase_current import STATUS_READY, SitePhaseCurrentStatus, build_site_phase_current_status

OPTION_SITE_PHASE_CAPACITY = "site_phase_capacity"
CONF_MAX_PHASE_CURRENT_A = "max_phase_current_a"

STATUS_NOT_CONFIGURED = "not_configured"
STATUS_SOURCE_NOT_READY = "source_not_ready"
STATUS_WITHIN_LIMIT = "within_limit"
STATUS_OVER_LIMIT = "over_limit"


@dataclass(frozen=True, slots=True)
class SitePhaseCapacitySettings:
    max_phase_current_a: float | None = None

    def validated(self) -> "SitePhaseCapacitySettings":
        if self.max_phase_current_a is not None:
            value = float(self.max_phase_current_a)
            if not math.isfinite(value) or value <= 0:
                raise ValueError("max_phase_current_a must be a finite positive number")
        return self

    def as_dict(self) -> dict[str, float | None]:
        return asdict(self)

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> "SitePhaseCapacitySettings":
        raw = options.get(OPTION_SITE_PHASE_CAPACITY)
        if not isinstance(raw, Mapping):
            return cls()
        value = raw.get(CONF_MAX_PHASE_CURRENT_A)
        if value in (None, "") or isinstance(value, bool):
            return cls()
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return cls()
        if not math.isfinite(parsed) or parsed <= 0:
            return cls()
        return cls(max_phase_current_a=parsed)


def update_site_phase_capacity_limit(
    options: Mapping[str, Any],
    max_phase_current_a: float | None,
) -> dict[str, Any]:
    settings = SitePhaseCapacitySettings(max_phase_current_a=max_phase_current_a).validated()
    updated = dict(options)
    updated[OPTION_SITE_PHASE_CAPACITY] = settings.as_dict()
    return updated


@dataclass(frozen=True, slots=True)
class PhaseCapacityValue:
    phase: str
    current_a: float | None
    max_current_a: float | None
    headroom_a: float | None
    over_limit_a: float | None
    utilization_percent: float | None
    over_limit: bool
    source_entity_id: str | None
    source_available: bool
    source_fresh: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SitePhaseCapacityStatus:
    entry_id: str
    status: str
    configured: bool
    max_phase_current_a: float | None
    phase_current_status: str
    source_ready: bool
    phases: dict[str, PhaseCapacityValue]
    worst_phase: str | None
    max_utilization_percent: float | None
    any_phase_over_limit: bool
    reason: str
    read_only: bool = True
    state_transition_performed: bool = False
    service_call_performed: bool = False
    execution_performed: bool = False
    execution_guard_active: bool = False

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["phases"] = {phase: value.as_dict() for phase, value in self.phases.items()}
        return result


def _diagnostic_phase_values(
    current: SitePhaseCurrentStatus,
    limit: float | None,
) -> dict[str, PhaseCapacityValue]:
    values: dict[str, PhaseCapacityValue] = {}
    source_ready = current.status == STATUS_READY
    for phase, source in current.phases.items():
        headroom = None
        over = None
        utilization = None
        is_over = False
        reason = source.reason
        if source_ready and limit is not None and source.current_a is not None:
            headroom = max(0.0, limit - source.current_a)
            over = max(0.0, source.current_a - limit)
            utilization = (source.current_a / limit) * 100.0
            is_over = over > 0
            reason = "over_limit" if is_over else "within_limit"
        values[phase] = PhaseCapacityValue(
            phase=phase,
            current_a=source.current_a,
            max_current_a=limit,
            headroom_a=headroom,
            over_limit_a=over,
            utilization_percent=utilization,
            over_limit=is_over,
            source_entity_id=source.entity_id,
            source_available=source.source_available,
            source_fresh=source.source_fresh,
            reason=reason,
        )
    return values


def build_site_phase_capacity_status(
    hass: HomeAssistant,
    *,
    entry_id: str,
    options: Mapping[str, Any],
    now: datetime | None = None,
) -> SitePhaseCapacityStatus:
    """Calculate per-phase headroom without creating execution authority."""
    if not entry_id:
        raise ValueError("entry_id is required")
    settings = SitePhaseCapacitySettings.from_options(options)
    limit = settings.max_phase_current_a
    current = build_site_phase_current_status(
        hass,
        entry_id=entry_id,
        options=options,
        now=now,
    )
    phases = _diagnostic_phase_values(current, limit)
    configured = limit is not None
    source_ready = current.status == STATUS_READY

    if not configured:
        status = STATUS_NOT_CONFIGURED
        reason = "Maximální proud jedné fáze není nastavený."
    elif not source_ready:
        status = STATUS_SOURCE_NOT_READY
        reason = (
            "Třífázová proudová diagnostika není připravená; limit se proto nepoužívá "
            "k výpočtu bezpečné rezervy."
        )
    else:
        over_phases = [phase for phase, item in phases.items() if item.over_limit]
        status = STATUS_OVER_LIMIT if over_phases else STATUS_WITHIN_LIMIT
        reason = (
            f"Limit proudu překračuje fáze {', '.join(over_phases)}."
            if over_phases
            else "Všechny tři fáze jsou v nastaveném proudovém limitu."
        )

    usable = [
        (phase, item.utilization_percent)
        for phase, item in phases.items()
        if item.utilization_percent is not None
    ]
    worst_phase = max(usable, key=lambda item: item[1])[0] if usable else None
    max_utilization = max((value for _, value in usable), default=None)

    return SitePhaseCapacityStatus(
        entry_id=entry_id,
        status=status,
        configured=configured,
        max_phase_current_a=limit,
        phase_current_status=current.status,
        source_ready=source_ready,
        phases=phases,
        worst_phase=worst_phase,
        max_utilization_percent=max_utilization,
        any_phase_over_limit=any(item.over_limit for item in phases.values()),
        reason=reason,
        execution_guard_active=configured,
    )
