from __future__ import annotations

from typing import Any

import pytest

from custom_components.frakon_energy.load_execution_capacity_reservation import (
    CapacityReservationError,
    CapacityReservationRepository,
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
            raise RuntimeError("reservation store unavailable")
        self.data = data


@pytest.mark.asyncio
async def test_reservation_survives_repository_reconstruction_and_is_idempotent() -> None:
    store = _Store()
    first = CapacityReservationRepository(store)

    reserved, created = await first.async_reserve(
        lifecycle_id="life-a",
        attempt_id="attempt-a",
        power_kw=11.0,
        now=100,
        ttl_seconds=300,
    )
    replay, replay_created = await first.async_reserve(
        lifecycle_id="life-a",
        attempt_id="attempt-a",
        power_kw=11.0,
        now=101,
        ttl_seconds=300,
    )

    assert created is True
    assert replay_created is False
    assert replay == reserved
    assert store.saves == 1

    reconstructed = CapacityReservationRepository(store)
    active = await reconstructed.async_active(now=150)
    assert active == (reserved,)


@pytest.mark.asyncio
async def test_read_only_snapshot_filters_expired_without_compacting_storage() -> None:
    store = _Store()
    repository = CapacityReservationRepository(store)
    active, _ = await repository.async_reserve(
        lifecycle_id="life-active",
        attempt_id="attempt-active",
        power_kw=7.0,
        now=100,
        ttl_seconds=300,
    )
    await repository.async_reserve(
        lifecycle_id="life-expired",
        attempt_id="attempt-expired",
        power_kw=4.0,
        now=100,
        ttl_seconds=10,
    )
    saves_before = store.saves
    persisted_before = store.data

    snapshot = await repository.async_snapshot(now=150)

    assert snapshot == (active,)
    assert store.saves == saves_before
    assert store.data == persisted_before
    assert store.data is not None
    assert len(store.data["reservations"]) == 2


@pytest.mark.asyncio
async def test_expired_reservation_is_ignored_and_compacted() -> None:
    store = _Store()
    repository = CapacityReservationRepository(store)
    await repository.async_reserve(
        lifecycle_id="life-a",
        attempt_id="attempt-a",
        power_kw=11.0,
        now=100,
        ttl_seconds=10,
    )

    active = await repository.async_active(now=111)

    assert active == ()
    assert store.data == {"schema_version": 1, "reservations": []}
    assert store.saves == 2


@pytest.mark.asyncio
async def test_binding_mismatch_cannot_reuse_existing_reservation() -> None:
    repository = CapacityReservationRepository(_Store())
    await repository.async_reserve(
        lifecycle_id="life-a",
        attempt_id="attempt-a",
        power_kw=11.0,
        now=100,
    )

    with pytest.raises(CapacityReservationError, match="binding mismatch"):
        await repository.async_reserve(
            lifecycle_id="life-a",
            attempt_id="attempt-b",
            power_kw=7.0,
            now=101,
        )


@pytest.mark.asyncio
async def test_failed_persistence_does_not_create_in_memory_reservation() -> None:
    store = _Store()
    repository = CapacityReservationRepository(store)
    store.fail = True

    with pytest.raises(RuntimeError, match="store unavailable"):
        await repository.async_reserve(
            lifecycle_id="life-a",
            attempt_id="attempt-a",
            power_kw=11.0,
            now=100,
        )

    store.fail = False
    assert await repository.async_active(now=101) == ()
