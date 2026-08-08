from __future__ import annotations

import pytest

from custom_components.frakon_energy.load_execution_site_capacity_gate import (
    CAPACITY_GATE_BLOCKED,
    CAPACITY_GATE_BYPASSED,
    CAPACITY_GATE_READY,
    REASON_GUARD_DISABLED,
    REASON_INSUFFICIENT_HEADROOM,
    REASON_NOT_CONFIGURED,
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


def test_configured_limit_is_diagnostic_until_guard_is_enabled() -> None:
    decision = evaluate_site_capacity_execution_gate(capacity=_capacity(guard=False, headroom=0.5), planned_power_kw=11.0)
    assert decision.status == CAPACITY_GATE_BYPASSED
    assert decision.reason == REASON_GUARD_DISABLED
    assert decision.can_start is True
    assert decision.guard_active is False


def test_enabled_guard_blocks_insufficient_headroom() -> None:
    decision = evaluate_site_capacity_execution_gate(capacity=_capacity(guard=True), planned_power_kw=11.0)
    assert decision.status == CAPACITY_GATE_BLOCKED
    assert decision.reason == REASON_INSUFFICIENT_HEADROOM
    assert decision.can_start is False
    assert decision.projected_grid_import_kw == pytest.approx(16.0)


def test_enabled_guard_allows_sufficient_headroom() -> None:
    decision = evaluate_site_capacity_execution_gate(capacity=_capacity(guard=True), planned_power_kw=6.0)
    assert decision.status == CAPACITY_GATE_READY
    assert decision.reason == REASON_READY
    assert decision.can_start is True


def test_corrupt_enabled_guard_without_limit_fails_closed() -> None:
    decision = evaluate_site_capacity_execution_gate(capacity=_capacity(guard=True, configured=False), planned_power_kw=1.0)
    assert decision.status == CAPACITY_GATE_BLOCKED
    assert decision.reason == REASON_NOT_CONFIGURED
    assert decision.can_start is False


def test_guard_cannot_be_enabled_without_limit() -> None:
    with pytest.raises(ValueError, match="requires max_grid_import_kw"):
        update_site_capacity_guard({}, True)


def test_limit_cannot_be_cleared_until_guard_is_disabled() -> None:
    options = update_site_capacity_limit({}, 12.0)
    options = update_site_capacity_guard(options, True)
    with pytest.raises(ValueError, match="disable site capacity execution guard"):
        update_site_capacity_limit(options, None)
    options = update_site_capacity_guard(options, False)
    options = update_site_capacity_limit(options, None)
    settings = SiteCapacitySettings.from_options(options)
    assert settings.max_grid_import_kw is None
    assert settings.execution_guard_enabled is False


def test_old_persisted_limit_without_boolean_defaults_to_guard_disabled() -> None:
    settings = SiteCapacitySettings.from_options({"site_capacity": {"max_grid_import_kw": 12.0}})
    assert settings.max_grid_import_kw == pytest.approx(12.0)
    assert settings.execution_guard_enabled is False
