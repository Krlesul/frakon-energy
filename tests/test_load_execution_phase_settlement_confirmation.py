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
