from __future__ import annotations

import pytest

from custom_components.frakon_energy.load_execution_phase_capacity_reservation import (
    PhaseCapacityReservation,
)
from custom_components.frakon_energy.load_execution_phase_settlement_evidence import (
    PhaseSettlementBaseline,
)
from custom_components.frakon_energy.load_execution_phase_settlement_proof import (
    STATUS_CANDIDATE,
    STATUS_ENTITY_CHANGED,
    STATUS_INCREASE_NOT_COVERED,
    STATUS_SAMPLE_NOT_NEWER,
    evaluate_phase_settlement_candidate,
)


def _reservation() -> PhaseCapacityReservation:
    return PhaseCapacityReservation(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        current_l1_a=8.0,
        current_l2_a=0.0,
        current_l3_a=4.0,
        created_at=100,
        expires_at=400,
    ).validated()


def _baseline() -> PhaseSettlementBaseline:
    return PhaseSettlementBaseline(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        entity_l1="sensor.l1",
        entity_l2="sensor.l2",
        entity_l3="sensor.l3",
        baseline_l1_a=10.0,
        baseline_l2_a=7.0,
        baseline_l3_a=5.0,
        observed_l1_at=90.0,
        observed_l2_at=91.0,
        observed_l3_at=92.0,
        created_at=100,
    ).validated()


def _entities() -> dict[str, str]:
    return {"L1": "sensor.l1", "L2": "sensor.l2", "L3": "sensor.l3"}


def test_candidate_requires_newer_samples_covering_full_reserved_increment() -> None:
    result = evaluate_phase_settlement_candidate(
        reservation=_reservation(),
        baseline=_baseline(),
        current_a={"L1": 18.0, "L2": 6.0, "L3": 9.0},
        entity_ids=_entities(),
        source_updated_at={"L1": 101.0, "L2": 91.0, "L3": 102.0},
    )

    assert result.status == STATUS_CANDIDATE
    assert result.candidate is True
    assert result.blocking_phases == ()
    assert result.required_current_a == {"L1": 18.0, "L2": 7.0, "L3": 9.0}
    assert result.reservation_release_performed is False
    assert result.state_transition_performed is False


def test_affected_phase_without_newer_sample_is_not_candidate() -> None:
    result = evaluate_phase_settlement_candidate(
        reservation=_reservation(),
        baseline=_baseline(),
        current_a={"L1": 18.0, "L2": 7.0, "L3": 9.0},
        entity_ids=_entities(),
        source_updated_at={"L1": 90.0, "L2": 200.0, "L3": 102.0},
    )

    assert result.status == STATUS_SAMPLE_NOT_NEWER
    assert result.candidate is False
    assert "L1" in result.blocking_phases


def test_new_sample_below_full_reserved_increment_is_not_candidate() -> None:
    result = evaluate_phase_settlement_candidate(
        reservation=_reservation(),
        baseline=_baseline(),
        current_a={"L1": 17.9, "L2": 100.0, "L3": 8.9},
        entity_ids=_entities(),
        source_updated_at={"L1": 101.0, "L2": 200.0, "L3": 102.0},
    )

    assert result.status == STATUS_INCREASE_NOT_COVERED
    assert result.candidate is False
    assert set(result.blocking_phases) == {"L1", "L3"}


def test_changed_confirmed_source_invalidates_baseline_proof() -> None:
    entities = _entities()
    entities["L3"] = "sensor.replacement_l3"
    result = evaluate_phase_settlement_candidate(
        reservation=_reservation(),
        baseline=_baseline(),
        current_a={"L1": 18.0, "L2": 7.0, "L3": 9.0},
        entity_ids=entities,
        source_updated_at={"L1": 101.0, "L2": 200.0, "L3": 102.0},
    )

    assert result.status == STATUS_ENTITY_CHANGED
    assert result.candidate is False
    assert result.blocking_phases == ("L3",)


def test_zero_reserved_phase_does_not_require_newer_sample_or_increment() -> None:
    result = evaluate_phase_settlement_candidate(
        reservation=_reservation(),
        baseline=_baseline(),
        current_a={"L1": 18.0, "L2": 0.0, "L3": 9.0},
        entity_ids=_entities(),
        source_updated_at={"L1": 101.0, "L2": 1.0, "L3": 102.0},
    )

    assert result.status == STATUS_CANDIDATE
    assert result.candidate is True
