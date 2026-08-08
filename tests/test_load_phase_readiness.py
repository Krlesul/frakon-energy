from custom_components.frakon_energy.load_phase_readiness import (
    REASON_PHASE_DATA_NOT_READY,
    REASON_PROJECTED_OVER_LIMIT,
    REASON_READY,
    STATUS_BLOCKED,
    STATUS_NOT_READY,
    STATUS_READY,
    evaluate_load_phase_readiness,
)
from custom_components.frakon_energy.load_profile_phase_projection import (
    LoadProfilePhaseProjection,
)


def _projection(*, status: str, can_evaluate: bool, over=(), worst="L1") -> LoadProfilePhaseProjection:
    return LoadProfilePhaseProjection(
        entry_id="entry-1",
        profile_id="ev",
        status=status,
        can_evaluate=can_evaluate,
        phase_topology="three_phase",
        capacity_status="within_limit",
        phases={},
        over_limit_phases=tuple(over),
        worst_phase=worst,
        reason="test",
    )


def test_within_limit_is_the_only_ready_state() -> None:
    decision = evaluate_load_phase_readiness(
        _projection(status="within_limit", can_evaluate=True, worst="L2")
    )

    assert decision.status == STATUS_READY
    assert decision.reason == REASON_READY
    assert decision.can_start_phase is True
    assert decision.can_evaluate is True
    assert decision.blocking_phases == ()
    assert decision.worst_phase == "L2"
    assert decision.execution_guard_active is False
    assert decision.service_call_performed is False


def test_projected_over_limit_blocks_exact_phases() -> None:
    decision = evaluate_load_phase_readiness(
        _projection(status="over_limit", can_evaluate=True, over=("L1", "L3"), worst="L1")
    )

    assert decision.status == STATUS_BLOCKED
    assert decision.reason == REASON_PROJECTED_OVER_LIMIT
    assert decision.can_start_phase is False
    assert decision.can_evaluate is True
    assert decision.blocking_phases == ("L1", "L3")


def test_unready_projection_fails_closed() -> None:
    decision = evaluate_load_phase_readiness(
        _projection(status="profile_not_ready", can_evaluate=False, worst=None)
    )

    assert decision.status == STATUS_NOT_READY
    assert decision.reason == REASON_PHASE_DATA_NOT_READY
    assert decision.can_start_phase is False
    assert decision.can_evaluate is False
    assert decision.blocking_phases == ()
    assert decision.execution_performed is False
    assert decision.reservation_performed is False


def test_inconsistent_non_evaluable_within_limit_still_fails_closed() -> None:
    decision = evaluate_load_phase_readiness(
        _projection(status="within_limit", can_evaluate=False)
    )

    assert decision.status == STATUS_NOT_READY
    assert decision.can_start_phase is False
