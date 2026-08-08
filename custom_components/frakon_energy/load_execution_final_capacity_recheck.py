"""Final fail-closed site-capacity recheck at the physical start boundary."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import time
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_capacity_reservation import CapacityReservation, CapacityReservationError, capacity_reservation_repository
from .load_execution_lifecycle import STATE_DISPATCHING, ExecutionLifecycleRecord
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_execution_site_capacity_gate import CAPACITY_GATE_BYPASSED, CAPACITY_GATE_READY, SiteCapacityGateDecision, evaluate_site_capacity_execution_gate
from .site_capacity import SiteCapacityStatus, build_site_capacity_status

FINAL_RECHECK_BYPASSED = "bypassed_guard_disabled"
FINAL_RECHECK_READY = "ready"
FINAL_RECHECK_BLOCKED = "blocked"
REASON_GUARD_DISABLED = "site_capacity_guard_disabled"
REASON_NOT_CONFIGURED = "site_capacity_limit_not_configured"
REASON_NO_DISPATCHING_LIFECYCLE = "dispatching_lifecycle_not_found"
REASON_MULTIPLE_DISPATCHING_LIFECYCLES = "multiple_dispatching_lifecycles"
REASON_RESERVATION_UNAVAILABLE = "site_capacity_reservation_unavailable"


class FinalCapacityRecheckError(RuntimeError):
    """Raised when the final site-capacity check cannot safely allow a start."""


@dataclass(frozen=True, slots=True)
class FinalCapacityRecheck:
    status: str
    reason: str
    lifecycle_id: str | None
    attempt_id: str | None
    planned_power_kw: float | None
    reserved_other_power_kw: float
    effective_planned_power_kw: float | None
    active_reservations: tuple[dict[str, Any], ...]
    reservation: dict[str, Any] | None
    capacity: dict[str, Any]
    capacity_gate: dict[str, Any] | None
    can_start: bool
    guard_active: bool
    read_only: bool
    state_transition_performed: bool
    service_call_performed: bool = False
    execution_performed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _entry(hass: HomeAssistant, entry_id: str):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise FinalCapacityRecheckError("FRAKON Energy config entry not found")
    return entry


async def _dispatching_records(hass: HomeAssistant, entry_id: str) -> list[ExecutionLifecycleRecord]:
    records = await lifecycle_repository(hass, entry_id).async_list()
    return [record for record in records if record.state == STATE_DISPATCHING]


def _result(*, status: str, reason: str, capacity: SiteCapacityStatus, can_start: bool, guard_active: bool, lifecycle: ExecutionLifecycleRecord | None = None, reserved_other_power_kw: float = 0.0, effective_planned_power_kw: float | None = None, active_reservations: tuple[CapacityReservation, ...] = (), reservation: CapacityReservation | None = None, capacity_gate: SiteCapacityGateDecision | None = None, reservation_created: bool = False) -> FinalCapacityRecheck:
    return FinalCapacityRecheck(
        status=status, reason=reason,
        lifecycle_id=lifecycle.lifecycle_id if lifecycle is not None else None,
        attempt_id=lifecycle.attempt_id if lifecycle is not None else None,
        planned_power_kw=lifecycle.plan.power_kw if lifecycle is not None else None,
        reserved_other_power_kw=reserved_other_power_kw,
        effective_planned_power_kw=effective_planned_power_kw,
        active_reservations=tuple(item.as_dict() for item in active_reservations),
        reservation=reservation.as_dict() if reservation is not None else None,
        capacity=capacity.as_dict(), capacity_gate=capacity_gate.as_dict() if capacity_gate is not None else None,
        can_start=can_start, guard_active=guard_active,
        read_only=not reservation_created, state_transition_performed=reservation_created,
    )


async def async_final_capacity_recheck(hass: HomeAssistant, *, entry_id: str) -> FinalCapacityRecheck:
    """Re-read live import and reserve capacity immediately before physical start."""
    if not entry_id:
        raise FinalCapacityRecheckError("entry_id is required")

    entry = _entry(hass, entry_id)
    capacity: SiteCapacityStatus = build_site_capacity_status(hass, entry_id=entry_id, options=entry.options)

    # A configured limit is diagnostic until the administrator explicitly enables the guard.
    # Bypass before lifecycle/reservation reads so a disabled guard creates no reservation state.
    if not capacity.execution_guard_active:
        return _result(status=FINAL_RECHECK_BYPASSED, reason=REASON_GUARD_DISABLED, capacity=capacity, can_start=True, guard_active=False)
    if not capacity.configured:
        return _result(status=FINAL_RECHECK_BLOCKED, reason=REASON_NOT_CONFIGURED, capacity=capacity, can_start=False, guard_active=True)

    dispatching = await _dispatching_records(hass, entry_id)
    if not dispatching:
        return _result(status=FINAL_RECHECK_BLOCKED, reason=REASON_NO_DISPATCHING_LIFECYCLE, capacity=capacity, can_start=False, guard_active=True)
    if len(dispatching) != 1:
        return _result(status=FINAL_RECHECK_BLOCKED, reason=REASON_MULTIPLE_DISPATCHING_LIFECYCLES, capacity=capacity, can_start=False, guard_active=True)

    lifecycle = dispatching[0]
    lifecycle.validated()
    now_ts = int(time.time())
    try:
        repository = capacity_reservation_repository(hass, entry_id)
        active = await repository.async_active(now=now_ts)
    except Exception as err:
        raise FinalCapacityRecheckError(f"capacity reservation state unavailable: {err}") from err

    other = tuple(item for item in active if item.lifecycle_id != lifecycle.lifecycle_id)
    reserved_other = sum(item.power_kw for item in other)
    effective_planned = lifecycle.plan.power_kw + reserved_other
    decision: SiteCapacityGateDecision = evaluate_site_capacity_execution_gate(capacity=capacity, planned_power_kw=effective_planned)
    allowed = decision.status in (CAPACITY_GATE_READY, CAPACITY_GATE_BYPASSED) and decision.can_start
    if not allowed:
        return _result(status=FINAL_RECHECK_BLOCKED, reason=decision.reason, capacity=capacity, can_start=False, guard_active=decision.guard_active, lifecycle=lifecycle, reserved_other_power_kw=reserved_other, effective_planned_power_kw=effective_planned, active_reservations=active, capacity_gate=decision)

    try:
        reservation, created = await repository.async_reserve(lifecycle_id=lifecycle.lifecycle_id, attempt_id=lifecycle.attempt_id, power_kw=lifecycle.plan.power_kw, now=now_ts)
    except CapacityReservationError as err:
        raise FinalCapacityRecheckError(f"capacity reservation could not be persisted: {err}") from err
    except Exception as err:
        raise FinalCapacityRecheckError(f"capacity reservation persistence unavailable: {err}") from err

    return _result(status=FINAL_RECHECK_READY, reason=decision.reason, capacity=capacity, can_start=True, guard_active=decision.guard_active, lifecycle=lifecycle, reserved_other_power_kw=reserved_other, effective_planned_power_kw=effective_planned, active_reservations=active, reservation=reservation, capacity_gate=decision, reservation_created=created)


async def async_require_final_capacity_recheck(hass: HomeAssistant, *, entry_id: str) -> FinalCapacityRecheck:
    result = await async_final_capacity_recheck(hass, entry_id=entry_id)
    if not result.can_start:
        raise FinalCapacityRecheckError(f"final site capacity recheck blocked start: {result.reason}")
    return result
