from __future__ import annotations

import pytest

from custom_components.frakon_energy.load_execution_site_capacity_gate import (
    CAPACITY_GATE_BLOCKED,
    CAPACITY_GATE_BYPASSED,
    CAPACITY_GATE_READY,
    REASON_GUARD_DISABLED,
    REASON_INSUFFICIENT_HEADROOM,
    REASON_LIMIT_MISSING,
    REASON_READY,
    evaluate_site_capacity_execution_gate,
)
from custom_components.frakon_energy.site_capacity import (
    SiteCapacitySettings,
    SiteCapacityStatus,
    update_site_capacity_guard,
    update_site_capacity_limit,
)


def _capacity(*, guard: bool, configured: bool = True, headroom: float = 7.0) -> SiteCapacityStatus:
    return SiteCapacityStatus(
        entry_id="entry-1",
        status="within_limit" if configured else "not_configured",
        configured=configured,
        topology_ready=True,
        source_available=True,
        max_grid_import_kw=12.0 if configured else None,
        current_grid_import_kw=5.0,
        grid_headroom_kw=headroom if configured else None,
        grid_over_limit_kw=0.0 if configured else None,
        utilization_percent=41.67 if configured else None,
        source_entity_id="sensor.grid_in",
        reason="ok",
        execution_guard_active=guard,
    )


def test_configured_limit_is_diagnostic_until_guard_is_explicitly_enabled() -> None:
    decision = evaluate_site_capacity_execution_gate(
        capacity=_capacity(guard=False, headroom=0.5),
        planned_power_kw=11.0,
    )
    assert decision.status == CAPACITY_GATE_BYPASSED
    assert decision.reason == REASON_GUARD_DISABLED
    assert decision.guard_active is False
    assert decision.can_start is True


def test_enabled_guard_blocks_insufficient_headroom() -> None:
    decision = evaluate_site_capacity_execution_gate(
        capacity=_capacity(guard=True, headroom=7.0),
        planned_power_kw=11.0,
    )
    assert decision.status == CAPACITY_GATE_BLOCKED
    assert decision.reason == REASON_INSUFFICIENT_HEADROOM
    assert decision.guard_active is True
    assert decision.can_start is False
    assert decision.projected_grid_import_kw == pytest.approx(16.0)


def test_enabled_guard_allows_sufficient_headroom() -> None:
    decision = evaluate_site_capacity_execution_gate(
        capacity=_capacity(guard=True, headroom=7.0),
        planned_power_kw=6.0,
    )
    assert decision.status == CAPACITY_GATE_READY
    assert decision.reason == REASON_READY
    assert decision.can_start is True
    assert decision.projected_grid_import_kw == pytest.approx(11.0)


def test_corrupt_enabled_guard_without_limit_fails_closed() -> None:
    decision = evaluate_site_capacity_execution_gate(
        capacity=_capacity(guard=True, configured=False),
        planned_power_kw=1.0,
    )
    assert decision.status == CAPACITY_GATE_BLOCKED
    assert decision.reason == REASON_LIMIT_MISSING
    assert decision.can_start is False


def test_guard_cannot_be_enabled_before_limit_is_configured() -> None:
    with pytest.raises(ValueError, match="requires max_grid_import_kw"):
        update_site_capacity_guard({}, True)


def test_limit_cannot_be_cleared_while_guard_is_active() -> None:
    options = update_site_capacity_limit({}, 12.0)
    options = update_site_capacity_guard(options, True)
    with pytest.raises(ValueError, match="disable site capacity execution guard"):
        update_site_capacity_limit(options, None)

    disabled = update_site_capacity_guard(options, False)
    cleared = update_site_capacity_limit(disabled, None)
    settings = SiteCapacitySettings.from_options(cleared)
    assert settings.execution_guard_enabled is False
    assert settings.max_grid_import_kw is None
