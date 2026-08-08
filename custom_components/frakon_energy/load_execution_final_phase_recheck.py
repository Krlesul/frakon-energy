"""Final fail-closed per-phase recheck at the physical start boundary."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_lifecycle import STATE_DISPATCHING, ExecutionLifecycleRecord
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_phase_readiness import LoadPhaseReadinessDecision, build_load_phase_readiness
from .site_phase_capacity import SitePhaseCapacityStatus, build_site_phase_capacity_status

FINAL_PHASE_RECHECK_BYPASSED = "bypassed_not_configured"
FINAL_PHASE_RECHECK_READY = "ready"
FINAL_PHASE_RECHECK_BLOCKED = "blocked"

REASON_NOT_CONFIGURED = "phase_capacity_limit_not_configured"
REASON_NO_DISPATCHING_LIFECYCLE = "dispatching_lifecycle_not_found"
REASON_MULTIPLE_DISPATCHING_LIFECYCLES = "multiple_dispatching_lifecycles"


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
    phase_readiness: dict[str, Any] | None
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


def _result(
    *,
    status: str,
    reason: str,
    capacity: SitePhaseCapacityStatus,
    can_start: bool,
    guard_active: bool,
    lifecycle: ExecutionLifecycleRecord | None = None,
    readiness: LoadPhaseReadinessDecision | None = None,
) -> FinalPhaseRecheck:
    return FinalPhaseRecheck(
        status=status,
        reason=reason,
        lifecycle_id=lifecycle.lifecycle_id if lifecycle is not None else None,
        attempt_id=lifecycle.attempt_id if lifecycle is not None else None,
        profile_id=lifecycle.profile_id if lifecycle is not None else None,
        phase_capacity=capacity.as_dict(),
        phase_readiness=readiness.as_dict() if readiness is not None else None,
        can_start=can_start,
        guard_active=guard_active,
    )


async def async_final_phase_recheck(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> FinalPhaseRecheck:
    """Re-read L1/L2/L3 immediately before the physical service-call boundary."""
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
    readiness = build_load_phase_readiness(
        hass,
        entry_id=entry_id,
        options=entry.options,
        profile_id=lifecycle.profile_id,
    )
    if not readiness.can_start_phase:
        return _result(
            status=FINAL_PHASE_RECHECK_BLOCKED,
            reason=readiness.reason,
            capacity=capacity,
            can_start=False,
            guard_active=True,
            lifecycle=lifecycle,
            readiness=readiness,
        )

    return _result(
        status=FINAL_PHASE_RECHECK_READY,
        reason=readiness.reason,
        capacity=capacity,
        can_start=True,
        guard_active=True,
        lifecycle=lifecycle,
        readiness=readiness,
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
