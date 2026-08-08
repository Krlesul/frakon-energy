"""Final fail-closed site-capacity recheck at the physical start boundary."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_lifecycle import STATE_DISPATCHING, ExecutionLifecycleRecord
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_execution_site_capacity_gate import (
    CAPACITY_GATE_BYPASSED,
    CAPACITY_GATE_READY,
    SiteCapacityGateDecision,
    evaluate_site_capacity_execution_gate,
)
from .site_capacity import SiteCapacityStatus, build_site_capacity_status

FINAL_RECHECK_BYPASSED = "bypassed_not_configured"
FINAL_RECHECK_READY = "ready"
FINAL_RECHECK_BLOCKED = "blocked"

REASON_NOT_CONFIGURED = "site_capacity_limit_not_configured"
REASON_NO_DISPATCHING_LIFECYCLE = "dispatching_lifecycle_not_found"
REASON_MULTIPLE_DISPATCHING_LIFECYCLES = "multiple_dispatching_lifecycles"


class FinalCapacityRecheckError(RuntimeError):
    """Raised when the final site-capacity check cannot safely allow a start."""


@dataclass(frozen=True, slots=True)
class FinalCapacityRecheck:
    status: str
    reason: str
    lifecycle_id: str | None
    attempt_id: str | None
    planned_power_kw: float | None
    capacity: dict[str, Any]
    capacity_gate: dict[str, Any] | None
    can_start: bool
    guard_active: bool
    read_only: bool = True
    state_transition_performed: bool = False
    service_call_performed: bool = False
    execution_performed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _entry(hass: HomeAssistant, entry_id: str):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise FinalCapacityRecheckError("FRAKON Energy config entry not found")
    return entry


async def _dispatching_records(
    hass: HomeAssistant,
    entry_id: str,
) -> list[ExecutionLifecycleRecord]:
    records = await lifecycle_repository(hass, entry_id).async_list()
    return [record for record in records if record.state == STATE_DISPATCHING]


async def async_final_capacity_recheck(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> FinalCapacityRecheck:
    """Re-read live grid import immediately before a physical bounded start."""
    if not entry_id:
        raise FinalCapacityRecheckError("entry_id is required")

    entry = _entry(hass, entry_id)
    capacity: SiteCapacityStatus = build_site_capacity_status(
        hass,
        entry_id=entry_id,
        options=entry.options,
    )

    if not capacity.configured:
        return FinalCapacityRecheck(
            status=FINAL_RECHECK_BYPASSED,
            reason=REASON_NOT_CONFIGURED,
            lifecycle_id=None,
            attempt_id=None,
            planned_power_kw=None,
            capacity=capacity.as_dict(),
            capacity_gate=None,
            can_start=True,
            guard_active=False,
        )

    dispatching = await _dispatching_records(hass, entry_id)
    if not dispatching:
        return FinalCapacityRecheck(
            status=FINAL_RECHECK_BLOCKED,
            reason=REASON_NO_DISPATCHING_LIFECYCLE,
            lifecycle_id=None,
            attempt_id=None,
            planned_power_kw=None,
            capacity=capacity.as_dict(),
            capacity_gate=None,
            can_start=False,
            guard_active=True,
        )
    if len(dispatching) != 1:
        return FinalCapacityRecheck(
            status=FINAL_RECHECK_BLOCKED,
            reason=REASON_MULTIPLE_DISPATCHING_LIFECYCLES,
            lifecycle_id=None,
            attempt_id=None,
            planned_power_kw=None,
            capacity=capacity.as_dict(),
            capacity_gate=None,
            can_start=False,
            guard_active=True,
        )

    lifecycle = dispatching[0]
    lifecycle.validated()
    decision: SiteCapacityGateDecision = evaluate_site_capacity_execution_gate(
        capacity=capacity,
        planned_power_kw=lifecycle.plan.power_kw,
    )
    allowed = decision.status in (CAPACITY_GATE_READY, CAPACITY_GATE_BYPASSED) and decision.can_start
    return FinalCapacityRecheck(
        status=FINAL_RECHECK_READY if allowed else FINAL_RECHECK_BLOCKED,
        reason=decision.reason,
        lifecycle_id=lifecycle.lifecycle_id,
        attempt_id=lifecycle.attempt_id,
        planned_power_kw=lifecycle.plan.power_kw,
        capacity=capacity.as_dict(),
        capacity_gate=decision.as_dict(),
        can_start=allowed,
        guard_active=decision.guard_active,
    )


async def async_require_final_capacity_recheck(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> FinalCapacityRecheck:
    """Fail closed unless the final capacity recheck explicitly allows start."""
    result = await async_final_capacity_recheck(hass, entry_id=entry_id)
    if not result.can_start:
        raise FinalCapacityRecheckError(
            f"final site capacity recheck blocked start: {result.reason}"
        )
    return result
