"""Read-only per-phase grid current snapshot for future phase-capacity safety."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
from typing import Any, Mapping

from homeassistant.core import HomeAssistant

from .entity_assignment_storage import load_entity_assignment_storage
from .entity_discovery import EntityRole
from .site_capacity import DEFAULT_CAPACITY_SOURCE_MAX_AGE_SECONDS
from .technology_profile import HouseTechnology

STATUS_NOT_CONFIGURED = "not_configured"
STATUS_PARTIAL = "partial"
STATUS_SOURCE_UNAVAILABLE = "source_unavailable"
STATUS_SOURCE_STALE = "source_stale"
STATUS_READY = "ready"

_PHASE_ROLES = {
    "L1": EntityRole.GRID_CURRENT_L1,
    "L2": EntityRole.GRID_CURRENT_L2,
    "L3": EntityRole.GRID_CURRENT_L3,
}


@dataclass(frozen=True, slots=True)
class PhaseCurrentValue:
    phase: str
    entity_id: str | None
    confirmed: bool
    current_a: float | None
    source_available: bool
    source_fresh: bool
    source_age_seconds: float | None
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SitePhaseCurrentStatus:
    entry_id: str
    status: str
    configured_phases: int
    mapping_complete: bool
    all_sources_available: bool
    all_sources_fresh: bool
    max_source_age_seconds: int
    phases: dict[str, PhaseCurrentValue]
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


def _aware_now(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return current


def _confirmed_phase_entities(options: Mapping[str, Any]) -> dict[str, str]:
    storage = load_entity_assignment_storage(options)
    result: dict[str, str] = {}
    for phase, role in _PHASE_ROLES.items():
        match = next(
            (
                item
                for item in storage.assignments
                if item.technology == HouseTechnology.SMART_METER
                and item.role == role
                and item.confirmed
            ),
            None,
        )
        if match is not None:
            result[phase] = match.entity_id
    return result


def _current_amps(state: Any) -> tuple[float | None, str]:
    if state is None:
        return None, "entity_missing"
    raw = str(getattr(state, "state", "")).strip().lower()
    if raw in {"", "unknown", "unavailable", "none"}:
        return None, "state_unavailable"
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, "state_not_numeric"
    if not math.isfinite(value):
        return None, "state_not_finite"
    unit = str(getattr(state, "attributes", {}).get("unit_of_measurement", "")).strip()
    if unit == "A":
        amps = value
    elif unit == "mA":
        amps = value / 1000.0
    else:
        return None, f"unsupported_unit:{unit or 'missing'}"
    if amps < 0:
        return None, "negative_current_not_supported"
    return amps, "ok"


def _freshness(state: Any, now: datetime) -> tuple[bool, float | None]:
    if state is None:
        return False, None
    last_updated = getattr(state, "last_updated", None)
    if not isinstance(last_updated, datetime):
        return False, None
    if last_updated.tzinfo is None or last_updated.utcoffset() is None:
        return False, None
    age = max(0.0, (now - last_updated).total_seconds())
    return age <= DEFAULT_CAPACITY_SOURCE_MAX_AGE_SECONDS, age


def build_site_phase_current_status(
    hass: HomeAssistant,
    *,
    entry_id: str,
    options: Mapping[str, Any],
    now: datetime | None = None,
) -> SitePhaseCurrentStatus:
    """Read confirmed L1/L2/L3 current sensors without inferring missing phases."""
    if not entry_id:
        raise ValueError("entry_id is required")
    current_time = _aware_now(now)
    mapped = _confirmed_phase_entities(options)
    phases: dict[str, PhaseCurrentValue] = {}

    for phase in ("L1", "L2", "L3"):
        entity_id = mapped.get(phase)
        if entity_id is None:
            phases[phase] = PhaseCurrentValue(
                phase=phase,
                entity_id=None,
                confirmed=False,
                current_a=None,
                source_available=False,
                source_fresh=False,
                source_age_seconds=None,
                reason="confirmed_phase_current_not_assigned",
            )
            continue
        state = hass.states.get(entity_id)
        amps, value_reason = _current_amps(state)
        fresh, age = _freshness(state, current_time)
        if amps is None:
            reason = value_reason
        elif not fresh:
            reason = "source_stale"
        else:
            reason = "ok"
        phases[phase] = PhaseCurrentValue(
            phase=phase,
            entity_id=entity_id,
            confirmed=True,
            current_a=amps,
            source_available=amps is not None,
            source_fresh=fresh,
            source_age_seconds=age,
            reason=reason,
        )

    configured = len(mapped)
    complete = configured == 3
    all_available = complete and all(item.source_available for item in phases.values())
    all_fresh = complete and all(item.source_fresh for item in phases.values())

    if configured == 0:
        status = STATUS_NOT_CONFIGURED
        reason = "Nejsou potvrzena měření proudu L1/L2/L3."
    elif not complete:
        status = STATUS_PARTIAL
        reason = "Pro bezpečný třífázový model musí být potvrzeny L1, L2 i L3."
    elif not all_available:
        status = STATUS_SOURCE_UNAVAILABLE
        reason = "Alespoň jedno potvrzené měření fáze nemá použitelnou hodnotu v A nebo mA."
    elif not all_fresh:
        status = STATUS_SOURCE_STALE
        reason = "Alespoň jedno potvrzené měření fáze je zastaralé."
    else:
        status = STATUS_READY
        reason = "Potvrzená měření proudu L1/L2/L3 jsou dostupná a čerstvá."

    return SitePhaseCurrentStatus(
        entry_id=entry_id,
        status=status,
        configured_phases=configured,
        mapping_complete=complete,
        all_sources_available=all_available,
        all_sources_fresh=all_fresh,
        max_source_age_seconds=DEFAULT_CAPACITY_SOURCE_MAX_AGE_SECONDS,
        phases=phases,
        reason=reason,
    )