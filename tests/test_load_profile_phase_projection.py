import pytest

from custom_components.frakon_energy.load_profile_phase_projection import (
    STATUS_CAPACITY_NOT_READY,
    STATUS_OVER_LIMIT,
    STATUS_PROFILE_NOT_READY,
    STATUS_WITHIN_LIMIT,
    project_load_profile_phase_capacity,
)
from custom_components.frakon_energy.load_profiles import (
    PHASE_TOPOLOGY_SINGLE,
    PHASE_TOPOLOGY_THREE,
    PROFILE_KIND_BOILER,
    PROFILE_KIND_EV,
    LoadProfile,
)
from custom_components.frakon_energy.site_phase_capacity import (
    PhaseCapacityValue,
    SitePhaseCapacityStatus,
)


def _phase(phase: str, current_a: float, limit: float = 25.0) -> PhaseCapacityValue:
    return PhaseCapacityValue(
        phase=phase,
        current_a=current_a,
        max_current_a=limit,
        headroom_a=max(0.0, limit - current_a),
        over_limit_a=max(0.0, current_a - limit),
        utilization_percent=(current_a / limit) * 100.0,
        over_limit=current_a > limit,
        source_entity_id=f"sensor.current_{phase.lower()}",
        source_available=True,
        source_fresh=True,
        reason="within_limit",
    )


def _capacity(*, l1: float = 10.0, l2: float = 12.0, l3: float = 14.0, ready: bool = True):
    phases = {
        "L1": _phase("L1", l1),
        "L2": _phase("L2", l2),
        "L3": _phase("L3", l3),
    }
    return SitePhaseCapacityStatus(
        entry_id="entry-1",
        status="within_limit" if ready else "source_not_ready",
        configured=True,
        max_phase_current_a=25.0,
        phase_current_status="ready" if ready else "source_stale",
        source_ready=ready,
        phases=phases,
        worst_phase="L3",
        max_utilization_percent=56.0,
        any_phase_over_limit=False,
        reason="ok" if ready else "stale",
    )


def test_three_phase_profile_projects_each_explicit_current_independently() -> None:
    profile = LoadProfile(
        "ev",
        "EV",
        PROFILE_KIND_EV,
        120,
        11.0,
        phase_topology=PHASE_TOPOLOGY_THREE,
        phase_current_l1_a=8.0,
        phase_current_l2_a=8.0,
        phase_current_l3_a=8.0,
    ).validated()

    result = project_load_profile_phase_capacity(
        entry_id="entry-1",
        profile=profile,
        capacity=_capacity(),
    )

    assert result.status == STATUS_WITHIN_LIMIT
    assert result.can_evaluate is True
    assert result.phases["L1"].projected_current_a == pytest.approx(18.0)
    assert result.phases["L2"].projected_current_a == pytest.approx(20.0)
    assert result.phases["L3"].projected_current_a == pytest.approx(22.0)
    assert result.over_limit_phases == ()
    assert result.execution_guard_active is False
    assert result.service_call_performed is False


def test_single_phase_profile_adds_current_only_to_explicit_phase() -> None:
    profile = LoadProfile(
        "boiler",
        "Bojler L2",
        PROFILE_KIND_BOILER,
        60,
        2.0,
        phase_topology=PHASE_TOPOLOGY_SINGLE,
        phase_current_l2_a=9.0,
    ).validated()

    result = project_load_profile_phase_capacity(
        entry_id="entry-1",
        profile=profile,
        capacity=_capacity(),
    )

    assert result.status == STATUS_WITHIN_LIMIT
    assert result.phases["L1"].planned_current_a == pytest.approx(0.0)
    assert result.phases["L1"].projected_current_a == pytest.approx(10.0)
    assert result.phases["L2"].planned_current_a == pytest.approx(9.0)
    assert result.phases["L2"].projected_current_a == pytest.approx(21.0)
    assert result.phases["L3"].planned_current_a == pytest.approx(0.0)


def test_projection_reports_exact_phase_that_would_exceed_limit() -> None:
    profile = LoadProfile(
        "boiler",
        "Bojler L1",
        PROFILE_KIND_BOILER,
        60,
        3.0,
        phase_topology=PHASE_TOPOLOGY_SINGLE,
        phase_current_l1_a=8.0,
    ).validated()

    result = project_load_profile_phase_capacity(
        entry_id="entry-1",
        profile=profile,
        capacity=_capacity(l1=20.0, l2=5.0, l3=5.0),
    )

    assert result.status == STATUS_OVER_LIMIT
    assert result.over_limit_phases == ("L1",)
    assert result.phases["L1"].projected_current_a == pytest.approx(28.0)
    assert result.phases["L1"].projected_over_limit_a == pytest.approx(3.0)
    assert result.phases["L2"].projected_over_limit_a == pytest.approx(0.0)


def test_unknown_profile_topology_is_not_evaluated_or_inferred_from_power() -> None:
    profile = LoadProfile("legacy", "Legacy EV", PROFILE_KIND_EV, 60, 11.0).validated()

    result = project_load_profile_phase_capacity(
        entry_id="entry-1",
        profile=profile,
        capacity=_capacity(),
    )

    assert result.status == STATUS_PROFILE_NOT_READY
    assert result.can_evaluate is False
    assert result.phases == {}
    assert result.over_limit_phases == ()


def test_stale_or_unready_site_phase_capacity_blocks_projection_without_guessing() -> None:
    profile = LoadProfile(
        "ev",
        "EV",
        PROFILE_KIND_EV,
        60,
        11.0,
        phase_topology=PHASE_TOPOLOGY_THREE,
        phase_current_l1_a=16.0,
        phase_current_l2_a=16.0,
        phase_current_l3_a=16.0,
    ).validated()

    result = project_load_profile_phase_capacity(
        entry_id="entry-1",
        profile=profile,
        capacity=_capacity(ready=False),
    )

    assert result.status == STATUS_CAPACITY_NOT_READY
    assert result.can_evaluate is False
    assert result.phases == {}
    assert result.execution_performed is False
