"""Read-only execution gate for explicitly enforced whole-site grid import capacity."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math

from .site_capacity import (
    STATUS_NOT_CONFIGURED,
    STATUS_OVER_LIMIT,
    STATUS_SOURCE_STALE,
    STATUS_SOURCE_UNAVAILABLE,
    STATUS_TOPOLOGY_NOT_READY,
    STATUS_WITHIN_LIMIT,
    SiteCapacityStatus,
)

CAPACITY_GATE_BYPASSED = "bypassed_not_configured"
CAPACITY_GATE_READY = "ready"
CAPACITY_GATE_BLOCKED = "blocked"

REASON_NOT_CONFIGURED = "site_capacity_limit_not_configured"
REASON_GUARD_DISABLED = "site_capacity_execution_guard_disabled"
REASON_READY = "site_capacity_headroom_sufficient"
REASON_TOPOLOGY = "site_capacity_topology_not_ready"
REASON_SOURCE = "site_capacity_source_unavailable"
REASON_SOURCE_STALE = "site_capacity_source_stale"
REASON_ALREADY_OVER_LIMIT = "site_capacity_already_over_limit"
REASON_INSUFFICIENT_HEADROOM = "site_capacity_headroom_insufficient"
REASON_INVALID_PLAN_POWER = "planned_power_invalid"


@dataclass(frozen=True, slots=True)
class SiteCapacityGateDecision:
    status: str
    reason: str
    capacity_status: str
    configured: bool
    planned_power_kw: float
    current_grid_import_kw: float | None
    max_grid_import_kw: float | None
    grid_headroom_kw: float | None
    projected_grid_import_kw: float | None
    projected_over_limit_kw: float | None
    can_start: bool
    guard_active: bool
    read_only: bool = True
    state_transition_performed: bool = False
    service_call_performed: bool = False
    execution_performed: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_site_capacity_execution_gate(
    *,
    capacity: SiteCapacityStatus,
    planned_power_kw: float,
) -> SiteCapacityGateDecision:
    """Fail closed only when explicit capacity enforcement is active."""
    if isinstance(planned_power_kw, bool) or not math.isfinite(planned_power_kw) or planned_power_kw <= 0:
        return SiteCapacityGateDecision(
            status=CAPACITY_GATE_BLOCKED,
            reason=REASON_INVALID_PLAN_POWER,
            capacity_status=capacity.status,
            configured=capacity.configured,
            planned_power_kw=float(planned_power_kw),
            current_grid_import_kw=capacity.current_grid_import_kw,
            max_grid_import_kw=capacity.max_grid_import_kw,
            grid_headroom_kw=capacity.grid_headroom_kw,
            projected_grid_import_kw=None,
            projected_over_limit_kw=None,
            can_start=False,
            guard_active=capacity.execution_guard_active,
        )

    base = dict(
        capacity_status=capacity.status,
        configured=capacity.configured,
        planned_power_kw=float(planned_power_kw),
        current_grid_import_kw=capacity.current_grid_import_kw,
        max_grid_import_kw=capacity.max_grid_import_kw,
        grid_headroom_kw=capacity.grid_headroom_kw,
    )

    if capacity.status == STATUS_NOT_CONFIGURED or not capacity.configured:
        return SiteCapacityGateDecision(
            status=CAPACITY_GATE_BYPASSED,
            reason=REASON_NOT_CONFIGURED,
            projected_grid_import_kw=None,
            projected_over_limit_kw=None,
            can_start=True,
            guard_active=False,
            **base,
        )

    if not capacity.execution_guard_active:
        return SiteCapacityGateDecision(
            status=CAPACITY_GATE_BYPASSED,
            reason=REASON_GUARD_DISABLED,
            projected_grid_import_kw=None,
            projected_over_limit_kw=None,
            can_start=True,
            guard_active=False,
            **base,
        )

    if capacity.status == STATUS_TOPOLOGY_NOT_READY or not capacity.topology_ready:
        return SiteCapacityGateDecision(
            status=CAPACITY_GATE_BLOCKED,
            reason=REASON_TOPOLOGY,
            projected_grid_import_kw=None,
            projected_over_limit_kw=None,
            can_start=False,
            guard_active=True,
            **base,
        )

    if capacity.status == STATUS_SOURCE_UNAVAILABLE or not capacity.source_available:
        return SiteCapacityGateDecision(
            status=CAPACITY_GATE_BLOCKED,
            reason=REASON_SOURCE,
            projected_grid_import_kw=None,
            projected_over_limit_kw=None,
            can_start=False,
            guard_active=True,
            **base,
        )

    if capacity.status == STATUS_SOURCE_STALE or not capacity.source_fresh:
        return SiteCapacityGateDecision(
            status=CAPACITY_GATE_BLOCKED,
            reason=REASON_SOURCE_STALE,
            projected_grid_import_kw=None,
            projected_over_limit_kw=None,
            can_start=False,
            guard_active=True,
            **base,
        )

    if capacity.status == STATUS_OVER_LIMIT:
        return SiteCapacityGateDecision(
            status=CAPACITY_GATE_BLOCKED,
            reason=REASON_ALREADY_OVER_LIMIT,
            projected_grid_import_kw=capacity.current_grid_import_kw,
            projected_over_limit_kw=capacity.grid_over_limit_kw,
            can_start=False,
            guard_active=True,
            **base,
        )

    if (
        capacity.status != STATUS_WITHIN_LIMIT
        or capacity.current_grid_import_kw is None
        or capacity.max_grid_import_kw is None
        or capacity.grid_headroom_kw is None
    ):
        return SiteCapacityGateDecision(
            status=CAPACITY_GATE_BLOCKED,
            reason=REASON_SOURCE,
            projected_grid_import_kw=None,
            projected_over_limit_kw=None,
            can_start=False,
            guard_active=True,
            **base,
        )

    projected = capacity.current_grid_import_kw + planned_power_kw
    projected_over = max(0.0, projected - capacity.max_grid_import_kw)
    if planned_power_kw > capacity.grid_headroom_kw + 1e-9:
        return SiteCapacityGateDecision(
            status=CAPACITY_GATE_BLOCKED,
            reason=REASON_INSUFFICIENT_HEADROOM,
            projected_grid_import_kw=projected,
            projected_over_limit_kw=projected_over,
            can_start=False,
            guard_active=True,
            **base,
        )

    return SiteCapacityGateDecision(
        status=CAPACITY_GATE_READY,
        reason=REASON_READY,
        projected_grid_import_kw=projected,
        projected_over_limit_kw=0.0,
        can_start=True,
        guard_active=True,
        **base,
    )