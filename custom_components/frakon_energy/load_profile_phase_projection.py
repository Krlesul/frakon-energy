"""Read-only projection of a load profile onto live per-phase site current."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from homeassistant.core import HomeAssistant

from .load_profiles import LoadProfile, profile_by_id
from .site_phase_capacity import (
    STATUS_WITHIN_LIMIT as PHASE_CAPACITY_WITHIN_LIMIT,
    SitePhaseCapacityStatus,
    build_site_phase_capacity_status,
)

STATUS_CAPACITY_NOT_READY = "capacity_not_ready"
STATUS_PROFILE_NOT_READY = "profile_not_ready"
STATUS_WITHIN_LIMIT = "within_limit"
STATUS_OVER_LIMIT = "over_limit"


@dataclass(frozen=True, slots=True)
class ProjectedPhaseValue:
    phase: str
    current_a: float
    planned_current_a: float
    projected_current_a: float
    max_current_a: float
    projected_headroom_a: float
    projected_over_limit_a: float
    over_limit: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LoadProfilePhaseProjection:
    entry_id: str
    profile_id: str
    status: str
    can_evaluate: bool
    phase_topology: str
    capacity_status: str
    phases: dict[str, ProjectedPhaseValue]
    over_limit_phases: tuple[str, ...]
    worst_phase: str | None
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


def _not_ready(
    *,
    entry_id: str,
    profile: LoadProfile,
    capacity: SitePhaseCapacityStatus,
    status: str,
    reason: str,
) -> LoadProfilePhaseProjection:
    return LoadProfilePhaseProjection(
        entry_id=entry_id,
        profile_id=profile.profile_id,
        status=status,
        can_evaluate=False,
        phase_topology=profile.phase_topology,
        capacity_status=capacity.status,
        phases={},
        over_limit_phases=(),
        worst_phase=None,
        reason=reason,
    )


def project_load_profile_phase_capacity(
    *,
    entry_id: str,
    profile: LoadProfile,
    capacity: SitePhaseCapacityStatus,
) -> LoadProfilePhaseProjection:
    """Project explicit profile phase currents without creating execution authority."""
    if not entry_id:
        raise ValueError("entry_id is required")
    profile.validated()

    if not capacity.configured or not capacity.source_ready or capacity.max_phase_current_a is None:
        return _not_ready(
            entry_id=entry_id,
            profile=profile,
            capacity=capacity,
            status=STATUS_CAPACITY_NOT_READY,
            reason="Třífázová kapacita není kompletně nakonfigurovaná a připravená.",
        )
    if not profile.phase_model_ready:
        return _not_ready(
            entry_id=entry_id,
            profile=profile,
            capacity=capacity,
            status=STATUS_PROFILE_NOT_READY,
            reason="Profil nemá explicitně potvrzenou fázovou topologii a proudy.",
        )

    limit = capacity.max_phase_current_a
    planned = profile.phase_currents_a()
    projected: dict[str, ProjectedPhaseValue] = {}
    for phase in ("L1", "L2", "L3"):
        source = capacity.phases.get(phase)
        if source is None or source.current_a is None:
            return _not_ready(
                entry_id=entry_id,
                profile=profile,
                capacity=capacity,
                status=STATUS_CAPACITY_NOT_READY,
                reason=f"Chybí autoritativní proud fáze {phase}.",
            )
        planned_current = planned.get(phase)
        added = planned_current if planned_current is not None else 0.0
        projected_current = source.current_a + added
        over = max(0.0, projected_current - limit)
        projected[phase] = ProjectedPhaseValue(
            phase=phase,
            current_a=source.current_a,
            planned_current_a=added,
            projected_current_a=projected_current,
            max_current_a=limit,
            projected_headroom_a=max(0.0, limit - projected_current),
            projected_over_limit_a=over,
            over_limit=over > 0,
        )

    over_limit_phases = tuple(
        phase for phase in ("L1", "L2", "L3") if projected[phase].over_limit
    )
    worst_phase = max(
        ("L1", "L2", "L3"),
        key=lambda phase: projected[phase].projected_current_a / limit,
    )
    status = STATUS_OVER_LIMIT if over_limit_phases else STATUS_WITHIN_LIMIT
    reason = (
        f"Po startu by proudový limit překročily fáze {', '.join(over_limit_phases)}."
        if over_limit_phases
        else "Explicitní fázové proudy profilu se vejdou do aktuální diagnostické rezervy."
    )
    return LoadProfilePhaseProjection(
        entry_id=entry_id,
        profile_id=profile.profile_id,
        status=status,
        can_evaluate=True,
        phase_topology=profile.phase_topology,
        capacity_status=capacity.status,
        phases=projected,
        over_limit_phases=over_limit_phases,
        worst_phase=worst_phase,
        reason=reason,
    )


def build_load_profile_phase_projection(
    hass: HomeAssistant,
    *,
    entry_id: str,
    options: Mapping[str, Any],
    profile_id: str,
) -> LoadProfilePhaseProjection:
    """Build a projection from persisted profile and current site diagnostics."""
    profile = profile_by_id(options, profile_id)
    capacity = build_site_phase_capacity_status(
        hass,
        entry_id=entry_id,
        options=options,
    )
    return project_load_profile_phase_capacity(
        entry_id=entry_id,
        profile=profile,
        capacity=capacity,
    )
