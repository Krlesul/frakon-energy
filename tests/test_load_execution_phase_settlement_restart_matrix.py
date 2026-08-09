from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_phase_settlement_release as release
from custom_components.frakon_energy.load_execution_lifecycle import STATE_VERIFIED
from custom_components.frakon_energy.load_execution_phase_capacity_reservation import (
    PhaseCapacityReservationRepository,
)
from custom_components.frakon_energy.load_execution_phase_settlement_confirmation import (
    MIN_CONFIRMATION_INTERVAL_SECONDS,
    PhaseSettlementConfirmationRepository,
)
from custom_components.frakon_energy.load_execution_phase_settlement_evidence import (
    PhaseSettlementBaseline,
)
from custom_components.frakon_energy.load_execution_phase_settlement_proof import (
    STATUS_CANDIDATE,
    evaluate_phase_settlement_candidate,
)


class _Store:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.saves = 0

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        self.saves += 1
        self.data = data


class _LifecycleRepo:
    async def async_list(self):
        return [
            SimpleNamespace(
                lifecycle_id="life-1",
                attempt_id="attempt-1",
                state=STATE_VERIFIED,
            )
        ]


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


@pytest.mark.asyncio
async def test_durable_reservation_and_confirmation_survive_restart_and_release_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reservation_store = _Store()
    confirmation_store = _Store()

    reservation_repo = PhaseCapacityReservationRepository(reservation_store)
    reservation, created = await reservation_repo.async_reserve(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        current_l1_a=8.0,
        current_l2_a=0.0,
        current_l3_a=4.0,
        now=100,
        ttl_seconds=300,
    )
    assert created is True

    baseline = _baseline()
    first_proof = evaluate_phase_settlement_candidate(
        reservation=reservation,
        baseline=baseline,
        current_a={"L1": 18.0, "L2": 7.0, "L3": 9.0},
        entity_ids=_entities(),
        source_updated_at={"L1": 101.0, "L2": 91.0, "L3": 102.0},
    )
    assert first_proof.status == STATUS_CANDIDATE
    assert first_proof.candidate is True

    confirmation_repo = PhaseSettlementConfirmationRepository(confirmation_store)
    first, first_created = await confirmation_repo.async_record_first(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        watermark=101.0,
        source_updated_at={"L1": 101.0, "L2": 91.0, "L3": 102.0},
        current_a={"L1": 18.0, "L2": 7.0, "L3": 9.0},
    )
    assert first_created is True
    assert first.confirmed_at is None

    restarted_reservation_repo = PhaseCapacityReservationRepository(reservation_store)
    restarted_confirmation_repo = PhaseSettlementConfirmationRepository(confirmation_store)
    active_after_restart = await restarted_reservation_repo.async_snapshot(now=104)
    restored_first = await restarted_confirmation_repo.async_get("life-1")
    assert active_after_restart == (reservation,)
    assert restored_first == first

    second_watermark = 101.0 + MIN_CONFIRMATION_INTERVAL_SECONDS
    second_proof = evaluate_phase_settlement_candidate(
        reservation=active_after_restart[0],
        baseline=baseline,
        current_a={"L1": 18.2, "L2": 7.0, "L3": 9.2},
        entity_ids=_entities(),
        source_updated_at={"L1": second_watermark, "L2": 91.0, "L3": second_watermark + 1.0},
    )
    assert second_proof.status == STATUS_CANDIDATE
    assert second_proof.candidate is True

    confirmed, confirmed_created = await restarted_confirmation_repo.async_confirm(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        watermark=second_watermark,
        confirmed_at=200,
    )
    assert confirmed_created is True
    assert confirmed.confirmed_at == 200

    final_reservation_repo = PhaseCapacityReservationRepository(reservation_store)
    final_confirmation_repo = PhaseSettlementConfirmationRepository(confirmation_store)
    assert (await final_confirmation_repo.async_get("life-1")) == confirmed

    monkeypatch.setattr(
        release,
        "phase_settlement_confirmation_repository",
        lambda hass, entry_id: final_confirmation_repo,
    )
    monkeypatch.setattr(release, "lifecycle_repository", lambda hass, entry_id: _LifecycleRepo())
    monkeypatch.setattr(
        release,
        "phase_capacity_reservation_repository",
        lambda hass, entry_id: final_reservation_repo,
    )

    async def final_proof(hass, *, entry_id: str, lifecycle_id: str):
        value = SimpleNamespace(candidate=True, reason="candidate_after_restart")
        value.as_dict = lambda: {"candidate": True, "reason": value.reason}
        return value

    monkeypatch.setattr(release, "async_phase_settlement_proof", final_proof)

    result = await release.async_release_confirmed_phase_reservation(
        object(),  # type: ignore[arg-type]
        entry_id="entry-1",
        lifecycle_id="life-1",
    )
    assert result.status == release.STATUS_RELEASED
    assert result.released is True

    after_release_restart = PhaseCapacityReservationRepository(reservation_store)
    assert await after_release_restart.async_snapshot(now=201) == ()

    monkeypatch.setattr(
        release,
        "phase_capacity_reservation_repository",
        lambda hass, entry_id: after_release_restart,
    )
    replay = await release.async_release_confirmed_phase_reservation(
        object(),  # type: ignore[arg-type]
        entry_id="entry-1",
        lifecycle_id="life-1",
    )
    assert replay.status == release.STATUS_ALREADY_ABSENT
    assert replay.released is False
