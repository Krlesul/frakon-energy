"""Read-only proof that newer phase telemetry has absorbed a reservation.

This module never releases reservations. It only decides whether a reservation is
eligible for a later, separately reviewed settlement step.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import time
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_phase_capacity_reservation import (
    PhaseCapacityReservation,
    phase_capacity_reservation_repository,
)
from .load_execution_phase_settlement_evidence import (
    PhaseSettlementBaseline,
    phase_settlement_evidence_repository,
)
from .site_phase_current import STATUS_READY, build_site_phase_current_status

STATUS_NOT_FOUND = "reservation_not_found"
STATUS_EVIDENCE_MISSING = "baseline_evidence_missing"
STATUS_SOURCE_NOT_READY = "source_not_ready"
STATUS_ENTITY_CHANGED = "source_entity_changed"
STATUS_SAMPLE_NOT_NEWER = "sample_not_newer"
STATUS_INCREASE_NOT_COVERED = "reserved_increase_not_covered"
STATUS_CANDIDATE = "settlement_candidate"


@dataclass(frozen=True, slots=True)
class PhaseSettlementProof:
    lifecycle_id: str
    status: str
    candidate: bool
    reason: str
    reservation: dict[str, Any] | None
    baseline: dict[str, Any] | None
    current_a: dict[str, float | None]
    required_current_a: dict[str, float | None]
    source_updated_at: dict[str, float | None]
    blocking_phases: tuple[str, ...]
    read_only: bool = True
    reservation_release_performed: bool = False
    state_transition_performed: bool = False
    service_call_performed: bool = False
    execution_performed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _empty(lifecycle_id: str, *, status: str, reason: str) -> PhaseSettlementProof:
    return PhaseSettlementProof(
        lifecycle_id=lifecycle_id,
        status=status,
        candidate=False,
        reason=reason,
        reservation=None,
        baseline=None,
        current_a={"L1": None, "L2": None, "L3": None},
        required_current_a={"L1": None, "L2": None, "L3": None},
        source_updated_at={"L1": None, "L2": None, "L3": None},
        blocking_phases=(),
    )


def evaluate_phase_settlement_candidate(
    *,
    reservation: PhaseCapacityReservation,
    baseline: PhaseSettlementBaseline,
    current_a: dict[str, float],
    entity_ids: dict[str, str],
    source_updated_at: dict[str, float],
) -> PhaseSettlementProof:
    """Require newer telemetry to cover the full reserved increment on every used phase."""
    reservation.validated()
    baseline.validated()
    if reservation.lifecycle_id != baseline.lifecycle_id or reservation.attempt_id != baseline.attempt_id:
        return _empty(
            reservation.lifecycle_id,
            status=STATUS_EVIDENCE_MISSING,
            reason="Reservation and baseline evidence binding do not match.",
        )

    reserved = reservation.currents()
    baseline_values = baseline.baselines()
    baseline_entities = baseline.entities()
    baseline_updated = baseline.observed_at()
    required = {
        phase: baseline_values[phase] + reserved[phase]
        for phase in ("L1", "L2", "L3")
    }
    blocking: list[str] = []
    status = STATUS_CANDIDATE
    reason = "Newer phase telemetry covers the complete reserved current on every affected phase."

    for phase in ("L1", "L2", "L3"):
        if entity_ids.get(phase) != baseline_entities[phase]:
            blocking.append(phase)
            status = STATUS_ENTITY_CHANGED
            reason = "A confirmed phase-current source changed after the reservation was created."
            continue
        if reserved[phase] <= 0:
            continue
        if source_updated_at.get(phase, 0.0) <= baseline_updated[phase]:
            blocking.append(phase)
            if status == STATUS_CANDIDATE:
                status = STATUS_SAMPLE_NOT_NEWER
                reason = "At least one affected phase has no newer telemetry sample than the pre-start baseline."
            continue
        if current_a.get(phase, -1.0) < required[phase]:
            blocking.append(phase)
            if status == STATUS_CANDIDATE:
                status = STATUS_INCREASE_NOT_COVERED
                reason = "New telemetry does not yet cover the complete reserved phase-current increase."

    return PhaseSettlementProof(
        lifecycle_id=reservation.lifecycle_id,
        status=status if blocking else STATUS_CANDIDATE,
        candidate=not blocking,
        reason=reason if blocking else "Newer phase telemetry covers the complete reserved current on every affected phase.",
        reservation=reservation.as_dict(),
        baseline=baseline.as_dict(),
        current_a={phase: current_a.get(phase) for phase in ("L1", "L2", "L3")},
        required_current_a=required,
        source_updated_at={phase: source_updated_at.get(phase) for phase in ("L1", "L2", "L3")},
        blocking_phases=tuple(blocking),
    )


async def async_phase_settlement_proof(
    hass: HomeAssistant,
    *,
    entry_id: str,
    lifecycle_id: str,
) -> PhaseSettlementProof:
    """Build settlement eligibility proof without mutating reservations or evidence."""
    if not entry_id or not lifecycle_id:
        raise ValueError("entry_id and lifecycle_id are required")
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ValueError("FRAKON Energy config entry not found")

    now_ts = int(time.time())
    reservations = await phase_capacity_reservation_repository(hass, entry_id).async_snapshot(now=now_ts)
    reservation = next((item for item in reservations if item.lifecycle_id == lifecycle_id), None)
    if reservation is None:
        return _empty(lifecycle_id, status=STATUS_NOT_FOUND, reason="No active phase reservation exists for this lifecycle.")

    baseline = await phase_settlement_evidence_repository(hass, entry_id).async_get(lifecycle_id)
    if baseline is None:
        result = _empty(lifecycle_id, status=STATUS_EVIDENCE_MISSING, reason="No durable pre-start baseline evidence exists for this reservation.")
        return PhaseSettlementProof(**{**result.as_dict(), "reservation": reservation.as_dict()})

    current = build_site_phase_current_status(
        hass,
        entry_id=entry_id,
        options=entry.options,
        now=datetime.now(timezone.utc),
    )
    if current.status != STATUS_READY:
        return PhaseSettlementProof(
            lifecycle_id=lifecycle_id,
            status=STATUS_SOURCE_NOT_READY,
            candidate=False,
            reason=current.reason,
            reservation=reservation.as_dict(),
            baseline=baseline.as_dict(),
            current_a={phase: current.phases[phase].current_a for phase in ("L1", "L2", "L3")},
            required_current_a={phase: None for phase in ("L1", "L2", "L3")},
            source_updated_at={phase: None for phase in ("L1", "L2", "L3")},
            blocking_phases=("L1", "L2", "L3"),
        )

    values: dict[str, float] = {}
    entities: dict[str, str] = {}
    updated: dict[str, float] = {}
    for phase in ("L1", "L2", "L3"):
        item = current.phases[phase]
        if item.current_a is None or not item.entity_id:
            raise ValueError(f"ready phase current unexpectedly missing {phase}")
        state = hass.states.get(item.entity_id)
        last_updated = getattr(state, "last_updated", None)
        if not isinstance(last_updated, datetime) or last_updated.tzinfo is None or last_updated.utcoffset() is None:
            raise ValueError(f"phase {phase} has no trustworthy last_updated timestamp")
        values[phase] = item.current_a
        entities[phase] = item.entity_id
        updated[phase] = last_updated.timestamp()

    return evaluate_phase_settlement_candidate(
        reservation=reservation,
        baseline=baseline,
        current_a=values,
        entity_ids=entities,
        source_updated_at=updated,
    )
