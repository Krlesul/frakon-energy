from __future__ import annotations

import pytest

from custom_components.frakon_energy.load_execution_site_capacity_gate import (
    CAPACITY_GATE_BLOCKED,
    CAPACITY_GATE_BYPASSED,
    CAPACITY_GATE_READY,
    REASON_ALREADY_OVER_LIMIT,
    REASON_GUARD_DISABLED,
    REASON_INSUFFICIENT_HEADROOM,
    REASON_NOT_CONFIGURED,
    REASON_READY,
    REASON_SOURCE,
    REASON_TOPOLOGY,
    evaluate_site_capacity_execution_gate,
)
from custom_components.frakon_energy.site_capacity import SiteCapacityStatus


def _capacity(
    *,
    status: str = "within_limit",
    configured: bool = True,
    topology_ready: bool = True,
    source_available: bool = True,
    limit: float | None = 12.0,
    current: float | None = 5.0,
    headroom: float | None = 7.0,
    over: float | None = 0.0,
    guard: bool = True,
) -> SiteCapacityStatus:
    return SiteCapacityStatus(
        entry_id="entry-1",
        status=status,
        configured=configured,
        topology_ready=topology_ready,
        source_available=source_available,
        max_grid_import_kw=limit,
        current_grid_import_kw=current,
        grid_headroom_kw=headroom,
        grid_over_limit_kw=over,
        utilization_percent=41.67 if limit and current is not None else None,
        source_entity_id="sensor.grid_in",
        reason="test",
        execution_guard_active=guard,
    )


def test_guard_disabled_bypasses_even_when_planned_load_exceeds_diagnostic_headroom() -> None:
    decision = evaluate_site_capacity_execution_gate(
        capacity=_capacity(guard=False, headroom=1.0),
        planned_power_kw=11.0,
    )
    assert decision.status == CAPACITY_GATE_BYPASSED
    assert decision.reason == REASON_GUARD_DISABLED
    assert decision.can_start is True
    assert decision.guard_active is False


def test_active_guard_allows_load_inside_headroom() -> None:
    decision = evaluate_site_capacity_execution_gate(
        capacity=_capacity(guard=True),
        planned_power_kw=6.0,
    )
    assert decision.status == CAPACITY_GATE_READY
    assert decision.reason == REASON_READY
    assert decision.can_start is True
    assert decision.projected_grid_import_kw == pytest.approx(11.0)


def test_active_guard_blocks_load_above_headroom() -> None:
    decision = evaluate_site_capacity_execution_gate(
        capacity=_capacity(guard=True),
        planned_power_kw=11.0,
    )
    assert decision.status == CAPACITY_GATE_BLOCKED
    assert decision.reason == REASON_INSUFFICIENT_HEADROOM
    assert decision.can_start is False
    assert decision.projected_grid_import_kw == pytest.approx(16.0)
    assert decision.projected_over_limit_kw == pytest.approx(4.0)


def test_active_guard_blocks_missing_limit_fail_closed() -> None:
    decision = evaluate_site_capacity_execution_gate(
        capacity=_capacity(status="not_configured", configured=False, limit=None, headroom=None, over=None, guard=True),
        planned_power_kw=1.0,
    )
    assert decision.status == CAPACITY_GATE_BLOCKED
    assert decision.reason == REASON_NOT_CONFIGURED
    assert decision.can_start is False


def test_active_guard_blocks_wrong_topology() -> None:
    decision = evaluate_site_capacity_execution_gate(
        capacity=_capacity(status="topology_not_ready", topology_ready=False, headroom=None, over=None, guard=True),
        planned_power_kw=1.0,
    )
    assert decision.status == CAPACITY_GATE_BLOCKED
    assert decision.reason == REASON_TOPOLOGY


def test_active_guard_blocks_unavailable_source() -> None:
    decision = evaluate_site_capacity_execution_gate(
        capacity=_capacity(status="source_unavailable", source_available=False, current=None, headroom=None, over=None, guard=True),
        planned_power_kw=1.0,
    )
    assert decision.status == CAPACITY_GATE_BLOCKED
    assert decision.reason == REASON_SOURCE


def test_active_guard_blocks_when_already_over_limit() -> None:
    decision = evaluate_site_capacity_execution_gate(
        capacity=_capacity(status="over_limit", current=13.0, headroom=0.0, over=1.0, guard=True),
        planned_power_kw=1.0,
    )
    assert decision.status == CAPACITY_GATE_BLOCKED
    assert decision.reason == REASON_ALREADY_OVER_LIMIT


@pytest.mark.parametrize("planned", [0.0, -1.0, float("inf"), float("nan")])
def test_invalid_plan_power_is_blocked_even_if_guard_disabled(planned: float) -> None:
    decision = evaluate_site_capacity_execution_gate(
        capacity=_capacity(guard=False),
        planned_power_kw=planned,
    )
    assert decision.status == CAPACITY_GATE_BLOCKED
    assert decision.can_start is False
