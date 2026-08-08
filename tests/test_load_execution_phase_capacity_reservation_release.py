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
async def test_exact_release_is_durable_and_idempotent() -> None:
    store = _Store()
    repo = PhaseCapacityReservationRepository(store)
    reservation, _ = await repo.async_reserve(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        current_l1_a=8.0,
        current_l2_a=0.0,
        current_l3_a=4.0,
        now=100,
    )

    released, changed = await repo.async_release(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
    )
    replay, replay_changed = await repo.async_release(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
    )

    assert released == reservation
    assert changed is True
    assert replay is None
    assert replay_changed is False
    assert await repo.async_snapshot(now=101) == ()

    reconstructed = PhaseCapacityReservationRepository(store)
    assert await reconstructed.async_snapshot(now=101) == ()


@pytest.mark.asyncio
async def test_release_rejects_attempt_binding_mismatch() -> None:
    repo = PhaseCapacityReservationRepository(_Store())
    await repo.async_reserve(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        current_l1_a=8.0,
        current_l2_a=0.0,
        current_l3_a=0.0,
        now=100,
    )

    with pytest.raises(PhaseCapacityReservationError, match="release binding mismatch"):
        await repo.async_release(
            lifecycle_id="life-1",
            attempt_id="attempt-other",
        )


@pytest.mark.asyncio
async def test_failed_release_save_keeps_reservation_published() -> None:
    store = _Store()
    repo = PhaseCapacityReservationRepository(store)
    reservation, _ = await repo.async_reserve(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        current_l1_a=8.0,
        current_l2_a=0.0,
        current_l3_a=0.0,
        now=100,
    )
    store.fail = True

    with pytest.raises(RuntimeError, match="store unavailable"):
        await repo.async_release(
            lifecycle_id="life-1",
            attempt_id="attempt-1",
        )

    store.fail = False
    assert await repo.async_snapshot(now=101) == (reservation,)
