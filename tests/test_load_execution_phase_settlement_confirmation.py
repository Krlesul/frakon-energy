from __future__ import annotations

from typing import Any

import pytest

from custom_components.frakon_energy.load_execution_phase_settlement_confirmation import (
    MIN_CONFIRMATION_INTERVAL_SECONDS,
    PhaseSettlementConfirmationError,
    PhaseSettlementConfirmationRepository,
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


async def _record(
    repo: PhaseSettlementConfirmationRepository,
    *,
    lifecycle_id: str,
    watermark: float,
    confirmed_at: int | None = None,
) -> None:
    suffix = lifecycle_id.removeprefix("life-")
    await repo.async_record_first(
        lifecycle_id=lifecycle_id,
        attempt_id=f"attempt-{suffix}",
        watermark=watermark,
        source_updated_at={"L1": watermark, "L2": watermark, "L3": watermark},
        current_a={"L1": 18.0, "L2": 7.0, "L3": 9.0},
    )
    if confirmed_at is not None:
        await repo.async_confirm(
            lifecycle_id=lifecycle_id,
            attempt_id=f"attempt-{suffix}",
            watermark=watermark + MIN_CONFIRMATION_INTERVAL_SECONDS,
            confirmed_at=confirmed_at,
        )


@pytest.mark.asyncio
async def test_first_observation_is_durable_and_idempotent() -> None:
    store = _Store()
    repo = PhaseSettlementConfirmationRepository(store)
    updates = {"L1": 101.0, "L2": 102.0, "L3": 103.0}
    currents = {"L1": 18.0, "L2": 7.0, "L3": 9.0}

    first, created = await repo.async_record_first(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        watermark=101.0,
        source_updated_at=updates,
        current_a=currents,
    )
    replay, replay_created = await repo.async_record_first(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        watermark=999.0,
        source_updated_at={"L1": 999.0, "L2": 999.0, "L3": 999.0},
        current_a={"L1": 99.0, "L2": 99.0, "L3": 99.0},
    )

    assert created is True
    assert replay_created is False
    assert replay == first
    assert first.confirmed_at is None
    assert store.saves == 1

    reconstructed = PhaseSettlementConfirmationRepository(store)
    assert await reconstructed.async_get("life-1") == first


@pytest.mark.asyncio
async def test_second_observation_must_be_separated_by_minimum_interval() -> None:
    repo = PhaseSettlementConfirmationRepository(_Store())
    await repo.async_record_first(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        watermark=100.0,
        source_updated_at={"L1": 100.0, "L2": 100.0, "L3": 100.0},
        current_a={"L1": 18.0, "L2": 7.0, "L3": 9.0},
    )

    with pytest.raises(PhaseSettlementConfirmationError, match="too close"):
        await repo.async_confirm(
            lifecycle_id="life-1",
            attempt_id="attempt-1",
            watermark=100.0 + MIN_CONFIRMATION_INTERVAL_SECONDS - 0.1,
            confirmed_at=200,
        )

    confirmed, created = await repo.async_confirm(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        watermark=100.0 + MIN_CONFIRMATION_INTERVAL_SECONDS,
        confirmed_at=200,
    )
    assert created is True
    assert confirmed.confirmed_at == 200
    assert confirmed.confirmed_watermark == pytest.approx(105.0)


@pytest.mark.asyncio
async def test_confirmed_observation_replay_is_idempotent() -> None:
    repo = PhaseSettlementConfirmationRepository(_Store())
    await repo.async_record_first(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        watermark=100.0,
        source_updated_at={"L1": 100.0, "L2": 100.0, "L3": 100.0},
        current_a={"L1": 18.0, "L2": 7.0, "L3": 9.0},
    )
    first, created = await repo.async_confirm(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        watermark=110.0,
        confirmed_at=200,
    )
    replay, replay_created = await repo.async_confirm(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        watermark=120.0,
        confirmed_at=300,
    )

    assert created is True
    assert replay_created is False
    assert replay == first


@pytest.mark.asyncio
async def test_failed_first_observation_save_does_not_publish_state() -> None:
    store = _Store()
    repo = PhaseSettlementConfirmationRepository(store)
    store.fail = True

    with pytest.raises(RuntimeError, match="store unavailable"):
        await repo.async_record_first(
            lifecycle_id="life-1",
            attempt_id="attempt-1",
            watermark=100.0,
            source_updated_at={"L1": 100.0, "L2": 100.0, "L3": 100.0},
            current_a={"L1": 18.0, "L2": 7.0, "L3": 9.0},
        )

    store.fail = False
    assert await repo.async_get("life-1") is None


@pytest.mark.asyncio
async def test_prune_preserves_active_and_newest_inactive_confirmations() -> None:
    store = _Store()
    repo = PhaseSettlementConfirmationRepository(store)
    for index in range(1, 7):
        await _record(
            repo,
            lifecycle_id=f"life-{index}",
            watermark=100.0 + index,
            confirmed_at=200 + index,
        )

    removed = await repo.async_prune(
        active_lifecycle_ids={"life-1", "life-2"},
        max_inactive=2,
    )

    assert removed == ("life-3", "life-4")
    assert await repo.async_get("life-1") is not None
    assert await repo.async_get("life-2") is not None
    assert await repo.async_get("life-3") is None
    assert await repo.async_get("life-4") is None
    assert await repo.async_get("life-5") is not None
    assert await repo.async_get("life-6") is not None

    reconstructed = PhaseSettlementConfirmationRepository(store)
    assert await reconstructed.async_get("life-1") is not None
    assert await reconstructed.async_get("life-3") is None
    assert await reconstructed.async_get("life-6") is not None


@pytest.mark.asyncio
async def test_failed_prune_save_does_not_publish_confirmation_cleanup() -> None:
    store = _Store()
    repo = PhaseSettlementConfirmationRepository(store)
    for index in range(1, 4):
        await _record(
            repo,
            lifecycle_id=f"life-{index}",
            watermark=100.0 + index,
            confirmed_at=200 + index,
        )

    store.fail = True
    with pytest.raises(RuntimeError, match="store unavailable"):
        await repo.async_prune(active_lifecycle_ids={"life-1"}, max_inactive=1)
    store.fail = False

    assert await repo.async_get("life-1") is not None
    assert await repo.async_get("life-2") is not None
    assert await repo.async_get("life-3") is not None
    reconstructed = PhaseSettlementConfirmationRepository(store)
    assert await reconstructed.async_get("life-1") is not None
    assert await reconstructed.async_get("life-2") is not None
    assert await reconstructed.async_get("life-3") is not None
