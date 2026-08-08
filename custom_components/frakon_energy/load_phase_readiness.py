"""Read-only phase-aware readiness for FRAKON Energy load profiles."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from homeassistant.core import HomeAssistant

from .load_profile_phase_projection import (
    STATUS_OVER_LIMIT as PROJECTION_OVER_LIMIT,
    STATUS_WITHIN_LIMIT as PROJECTION_WITHIN_LIMIT,
    LoadProfilePhaseProjection,
    build_load_profile_phase_projection,
)

STATUS_READY = "ready"
STATUS_BLOCKED = "blocked"
STATUS_NOT_READY = "not_ready"

REASON_READY = "phase_capacity_available"
REASON_PROJECTED_OVER_LIMIT = "projected_phase_limit_exceeded"
REASON_PHASE_DATA_NOT_READY = "phase_data_not_ready"


@dataclass(frozen=True, slots=True)
class LoadPhaseReadinessDecision:
    entry_id: str
    profile_id: str
    status: str
    reason: str
    can_start_phase: bool
    can_evaluate: bool
    projection_status: str
    blocking_phases: tuple[str, ...]
    worst_phase: str | None
    read_only: bool = True
    state_transition_performed: bool = False
    reservation_performed: bool = False
    service_call_performed: bool = False
    execution_performed: bool = False
    execution_guard_active: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_load_phase_readiness(
    projection: LoadProfilePhaseProjection,
) -> LoadPhaseReadinessDecision:
    """Convert an authoritative phase projection into a fail-closed readiness decision."""
    if projection.status == PROJECTION_WITHIN_LIMIT and projection.can_evaluate:
        return LoadPhaseReadinessDecision(
            entry_id=projection.entry_id,
            profile_id=projection.profile_id,
            status=STATUS_READY,
            reason=REASON_READY,
            can_start_phase=True,
            can_evaluate=True,
            projection_status=projection.status,
            blocking_phases=(),
            worst_phase=projection.worst_phase,
        )

    if projection.status == PROJECTION_OVER_LIMIT and projection.can_evaluate:
        return LoadPhaseReadinessDecision(
            entry_id=projection.entry_id,
            profile_id=projection.profile_id,
            status=STATUS_BLOCKED,
            reason=REASON_PROJECTED_OVER_LIMIT,
            can_start_phase=False,
            can_evaluate=True,
            projection_status=projection.status,
            blocking_phases=projection.over_limit_phases,
            worst_phase=projection.worst_phase,
        )

    return LoadPhaseReadinessDecision(
        entry_id=projection.entry_id,
        profile_id=projection.profile_id,
        status=STATUS_NOT_READY,
        reason=REASON_PHASE_DATA_NOT_READY,
        can_start_phase=False,
        can_evaluate=False,
        projection_status=projection.status,
        blocking_phases=(),
        worst_phase=projection.worst_phase,
    )


def build_load_phase_readiness(
    hass: HomeAssistant,
    *,
    entry_id: str,
    options: Mapping[str, Any],
    profile_id: str,
) -> LoadPhaseReadinessDecision:
    """Build read-only phase readiness from persisted profile and live phase capacity."""
    projection = build_load_profile_phase_projection(
        hass,
        entry_id=entry_id,
        options=options,
        profile_id=profile_id,
    )
    return evaluate_load_phase_readiness(projection)
