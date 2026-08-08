from __future__ import annotations

from typing import Any

import pytest

from custom_components.frakon_energy.load_execution_phase_capacity_reservation import (
    PhaseCapacityReservationError,
    PhaseCapacityReservationRepository,
)


class _Store:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.saves = 0
        self.fail = False

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        self.saves += 1
        if self.fail:
            raise RuntimeError("store unavailable")
        self.data = data


@pytest.mark.asyncio
async def test_reservation_is_durable_idempotent_and_bound_to_exact_currents() -> None:
    store = _Store()
    repo = PhaseCapacityReservationRepository(store)

    first, created = await repo.async_reserve(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        current_l1_a=8.0,
        current_l2_a=0.0,
        current_l3_a=0.0,
        now=100,
    )
    replay, replay_created = await repo.async_reserve(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        current_l1_a=8.0,
        current_l2_a=0.0,
        current_l3_a=0.0,
        now=101,
    )

    assert created is True
    assert replay_created is False
    assert replay == first
    assert first.currents() == {"L1": 8.0, "L2": 0.0, "L3": 0.0}
    assert store.saves == 1

    reconstructed = PhaseCapacityReservationRepository(store)
    active = await reconstructed.async_snapshot(now=102)
    assert active == (first,)

    with pytest.raises(PhaseCapacityReservationError, match="binding mismatch"):
        await reconstructed.async_reserve(
            lifecycle_id="life-1",
            attempt_id="attempt-1",
            current_l1_a=9.0,
            current_l2_a=0.0,
            current_l3_a=0.0,
            now=103,
        )


@pytest.mark.asyncio
async def test_snapshot_is_read_only_and_active_compacts_expired() -> None:
    store = _Store()
    repo = PhaseCapacityReservationRepository(store)
    await repo.async_reserve(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        current_l1_a=4.0,
        current_l2_a=4.0,
        current_l3_a=4.0,
        now=100,
        ttl_seconds=10,
    )
    assert store.saves == 1

    assert await repo.async_snapshot(now=111) == ()
    assert store.saves == 1

    assert await repo.async_active(now=111) == ()
    assert store.saves == 2


@pytest.mark.asyncio
async def test_invalid_or_empty_phase_currents_fail_closed() -> None:
    repo = PhaseCapacityReservationRepository(_Store())

    with pytest.raises(PhaseCapacityReservationError, match="at least one phase"):
        await repo.async_reserve(
            lifecycle_id="life-1",
            attempt_id="attempt-1",
            current_l1_a=0.0,
            current_l2_a=0.0,
            current_l3_a=0.0,
            now=100,
        )

    with pytest.raises(PhaseCapacityReservationError, match="non-negative"):
        await repo.async_reserve(
            lifecycle_id="life-2",
            attempt_id="attempt-2",
            current_l1_a=-1.0,
            current_l2_a=0.0,
            current_l3_a=0.0,
            now=100,
        )


@pytest.mark.asyncio
async def test_failed_save_does_not_publish_reservation_in_memory() -> None:
    store = _Store()
    repo = PhaseCapacityReservationRepository(store)
    store.fail = True

    with pytest.raises(RuntimeError, match="store unavailable"):
        await repo.async_reserve(
            lifecycle_id="life-1",
            attempt_id="attempt-1",
            current_l1_a=8.0,
            current_l2_a=0.0,
            current_l3_a=0.0,
            now=100,
        )

    store.fail = False
    assert await repo.async_snapshot(now=101) == ()
