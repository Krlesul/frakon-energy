"""Final fail-closed per-phase recheck at the physical start boundary."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import time
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_lifecycle import STATE_DISPATCHING, ExecutionLifecycleRecord
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_execution_phase_capacity_reservation import (
    PhaseCapacityReservation,
    PhaseCapacityReservationError,
    phase_capacity_reservation_repository,
)
from .load_execution_phase_settlement_evidence import (
    PhaseSettlementBaseline,
    phase_settlement_evidence_repository,
)
from .load_phase_readiness import (
    LoadPhaseReadinessDecision,
    evaluate_load_phase_readiness,
)
from .load_profile_phase_projection import (
    LoadProfilePhaseProjection,
    build_load_profile_phase_projection,
)
from .site_phase_capacity import SitePhaseCapacityStatus, build_site_phase_capacity_status

FINAL_PHASE_RECHECK_BYPASSED = "bypassed_not_configured"
FINAL_PHASE_RECHECK_READY = "ready"
FINAL_PHASE_RECHECK_BLOCKED = "blocked"

REASON_NOT_CONFIGURED = "phase_capacity_limit_not_configured"
REASON_NO_DISPATCHING_LIFECYCLE = "dispatching_lifecycle_not_found"
REASON_MULTIPLE_DISPATCHING_LIFECYCLES = "multiple_dispatching_lifecycles"
REASON_RESERVATION_UNAVAILABLE = "phase_capacity_reservation_unavailable"
REASON_RESERVED_HEADROOM_INSUFFICIENT = "reserved_phase_headroom_insufficient"


class FinalPhaseRecheckError(RuntimeError):
    """Raised when final per-phase safety cannot safely allow a start."""


@dataclass(frozen=True, slots=True)
class FinalPhaseRecheck:
    status: str
    reason: str
    lifecycle_id: str | None
    attempt_id: str | None
    profile_id: str | None
    phase_capacity: dict[str, Any]
    phase_projection: dict[str, Any] | None
    phase_readiness: dict[str, Any] | None
    active_reservations: tuple[dict[str, Any], ...]
    reserved_other_currents_a: dict[str, float]
    effective_projected_currents_a: dict[str, float]
    blocking_phases: tuple[str, ...]
    reservation: dict[str, Any] | None
    settlement_baseline: dict[str, Any] | None
    settlement_evidence_persisted: bool
    settlement_evidence_error: str | None
    can_start: bool
    guard_active: bool
    read_only: bool = True
    state_transition_performed: bool = False
    reservation_performed: bool = False
    service_call_performed: bool = False
    execution_performed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _entry(hass: HomeAssistant, entry_id: str):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise FinalPhaseRecheckError("FRAKON Energy config entry not found")
    return entry


async def _dispatching_records(
    hass: HomeAssistant,
    entry_id: str,
) -> list[ExecutionLifecycleRecord]:
    records = await lifecycle_repository(hass, entry_id).async_list()
    return [record for record in records if record.state == STATE_DISPATCHING]


def _sum_currents(reservations: tuple[PhaseCapacityReservation, ...]) -> dict[str, float]:
    totals = {"L1": 0.0, "L2": 0.0, "L3": 0.0}
    for reservation in reservations:
        for phase, value in reservation.currents().items():
            totals[phase] += value
    return totals


def _settlement_baseline(
    hass: HomeAssistant,
    *,
    lifecycle: ExecutionLifecycleRecord,
    capacity: SitePhaseCapacityStatus,
    created_at: int,
) -> PhaseSettlementBaseline | None:
    values: dict[str, tuple[str, float, float]] = {}
    for phase in ("L1", "L2", "L3"):
        item = capacity.phases.get(phase)
        if item is None or item.current_a is None or not item.source_entity_id:
            return None
        state = hass.states.get(item.source_entity_id)
        last_updated = getattr(state, "last_updated", None)
        if not isinstance(last_updated, datetime) or last_updated.tzinfo is None or last_updated.utcoffset() is None:
            return None
        values[phase] = (item.source_entity_id, item.current_a, last_updated.timestamp())
    return PhaseSettlementBaseline(
        lifecycle_id=lifecycle.lifecycle_id,
        attempt_id=lifecycle.attempt_id,
        entity_l1=values["L1"][0],
        entity_l2=values["L2"][0],
        entity_l3=values["L3"][0],
        baseline_l1_a=values["L1"][1],
        baseline_l2_a=values["L2"][1],
        baseline_l3_a=values["L3"][1],
        observed_l1_at=values["L1"][2],
        observed_l2_at=values["L2"][2],
        observed_l3_at=values["L3"][2],
        created_at=created_at,
    ).validated()


def _result(
    *,
    status: str,
    reason: str,
    capacity: SitePhaseCapacityStatus,
    can_start: bool,
    guard_active: bool,
    lifecycle: ExecutionLifecycleRecord | None = None,
    projection: LoadProfilePhaseProjection | None = None,
    readiness: LoadPhaseReadinessDecision | None = None,
    active_reservations: tuple[PhaseCapacityReservation, ...] = (),
    reserved_other_currents_a: dict[str, float] | None = None,
    effective_projected_currents_a: dict[str, float] | None = None,
    blocking_phases: tuple[str, ...] = (),
    reservation: PhaseCapacityReservation | None = None,
    reservation_created: bool = False,
    settlement_baseline: PhaseSettlementBaseline | None = None,
    settlement_evidence_persisted: bool = False,
    settlement_evidence_error: str | None = None,
) -> FinalPhaseRecheck:
    return FinalPhaseRecheck(
        status=status,
        reason=reason,
        lifecycle_id=lifecycle.lifecycle_id if lifecycle is not None else None,
        attempt_id=lifecycle.attempt_id if lifecycle is not None else None,
        profile_id=lifecycle.profile_id if lifecycle is not None else None,
        phase_capacity=capacity.as_dict(),
        phase_projection=projection.as_dict() if projection is not None else None,
        phase_readiness=readiness.as_dict() if readiness is not None else None,
        active_reservations=tuple(value.as_dict() for value in active_reservations),
        reserved_other_currents_a=reserved_other_currents_a or {"L1": 0.0, "L2": 0.0, "L3": 0.0},
        effective_projected_currents_a=effective_projected_currents_a or {},
        blocking_phases=blocking_phases,
        reservation=reservation.as_dict() if reservation is not None else None,
        settlement_baseline=settlement_baseline.as_dict() if settlement_baseline is not None else None,
        settlement_evidence_persisted=settlement_evidence_persisted,
        settlement_evidence_error=settlement_evidence_error,
        can_start=can_start,
        guard_active=guard_active,
        read_only=not reservation_created,
        state_transition_performed=reservation_created,
        reservation_performed=reservation_created,
    )


async def async_final_phase_recheck(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> FinalPhaseRecheck:
    """Re-read L1/L2/L3, account for reservations, then reserve before start."""
    if not entry_id:
        raise FinalPhaseRecheckError("entry_id is required")

    entry = _entry(hass, entry_id)
    capacity = build_site_phase_capacity_status(
        hass,
        entry_id=entry_id,
        options=entry.options,
    )
    if not capacity.configured:
        return _result(
            status=FINAL_PHASE_RECHECK_BYPASSED,
            reason=REASON_NOT_CONFIGURED,
            capacity=capacity,
            can_start=True,
            guard_active=False,
        )

    dispatching = await _dispatching_records(hass, entry_id)
    if not dispatching:
        return _result(
            status=FINAL_PHASE_RECHECK_BLOCKED,
            reason=REASON_NO_DISPATCHING_LIFECYCLE,
            capacity=capacity,
            can_start=False,
            guard_active=True,
        )
    if len(dispatching) != 1:
        return _result(
            status=FINAL_PHASE_RECHECK_BLOCKED,
            reason=REASON_MULTIPLE_DISPATCHING_LIFECYCLES,
            capacity=capacity,
            can_start=False,
            guard_active=True,
        )

    lifecycle = dispatching[0]
    lifecycle.validated()
    projection = build_load_profile_phase_projection(
        hass,
        entry_id=entry_id,
        options=entry.options,
        profile_id=lifecycle.profile_id,
    )
    readiness = evaluate_load_phase_readiness(projection)
    if not readiness.can_start_phase:
        return _result(
            status=FINAL_PHASE_RECHECK_BLOCKED,
            reason=readiness.reason,
            capacity=capacity,
            can_start=False,
            guard_active=True,
            lifecycle=lifecycle,
            projection=projection,
            readiness=readiness,
        )

    now_ts = int(time.time())
    try:
        repository = phase_capacity_reservation_repository(hass, entry_id)
        active = await repository.async_active(now=now_ts)
    except Exception as err:
        raise FinalPhaseRecheckError(f"phase capacity reservation state unavailable: {err}") from err

    other = tuple(value for value in active if value.lifecycle_id != lifecycle.lifecycle_id)
    reserved_other = _sum_currents(other)
    effective: dict[str, float] = {}
    blocking: list[str] = []
    for phase in ("L1", "L2", "L3"):
        phase_projection = projection.phases.get(phase)
        if phase_projection is None:
            raise FinalPhaseRecheckError(f"phase projection missing {phase}")
        value = phase_projection.projected_current_a + reserved_other[phase]
        effective[phase] = value
        if value > phase_projection.max_current_a:
            blocking.append(phase)

    if blocking:
        return _result(
            status=FINAL_PHASE_RECHECK_BLOCKED,
            reason=REASON_RESERVED_HEADROOM_INSUFFICIENT,
            capacity=capacity,
            can_start=False,
            guard_active=True,
            lifecycle=lifecycle,
            projection=projection,
            readiness=readiness,
            active_reservations=active,
            reserved_other_currents_a=reserved_other,
            effective_projected_currents_a=effective,
            blocking_phases=tuple(blocking),
        )

    planned = {
        phase: projection.phases[phase].planned_current_a
        for phase in ("L1", "L2", "L3")
    }
    try:
        reservation, created = await repository.async_reserve(
            lifecycle_id=lifecycle.lifecycle_id,
            attempt_id=lifecycle.attempt_id,
            current_l1_a=planned["L1"],
            current_l2_a=planned["L2"],
            current_l3_a=planned["L3"],
            now=now_ts,
        )
    except PhaseCapacityReservationError as err:
        raise FinalPhaseRecheckError(
            f"phase capacity reservation could not be persisted: {err}"
        ) from err
    except Exception as err:
        raise FinalPhaseRecheckError(
            f"phase capacity reservation persistence unavailable: {err}"
        ) from err

    baseline: PhaseSettlementBaseline | None = None
    evidence_persisted = False
    evidence_error: str | None = None
    if created:
        try:
            baseline = _settlement_baseline(
                hass,
                lifecycle=lifecycle,
                capacity=capacity,
                created_at=now_ts,
            )
            if baseline is None:
                evidence_error = "authoritative_phase_baseline_unavailable"
            else:
                _, evidence_persisted = await phase_settlement_evidence_repository(
                    hass, entry_id
                ).async_put(baseline)
        except Exception as err:
            evidence_error = str(err)

    return _result(
        status=FINAL_PHASE_RECHECK_READY,
        reason=readiness.reason,
        capacity=capacity,
        can_start=True,
        guard_active=True,
        lifecycle=lifecycle,
        projection=projection,
        readiness=readiness,
        active_reservations=active,
        reserved_other_currents_a=reserved_other,
        effective_projected_currents_a=effective,
        reservation=reservation,
        reservation_created=created,
        settlement_baseline=baseline,
        settlement_evidence_persisted=evidence_persisted,
        settlement_evidence_error=evidence_error,
    )


async def async_require_final_phase_recheck(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> FinalPhaseRecheck:
    """Fail closed unless final configured phase safety allows start."""
    result = await async_final_phase_recheck(hass, entry_id=entry_id)
    if not result.can_start:
        raise FinalPhaseRecheckError(
            f"final phase capacity recheck blocked start: {result.reason}"
        )
    return result
