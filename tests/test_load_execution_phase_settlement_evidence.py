from __future__ import annotations

from typing import Any

import pytest

from custom_components.frakon_energy.load_execution_phase_settlement_evidence import (
    PhaseSettlementBaseline,
    PhaseSettlementEvidenceError,
    PhaseSettlementEvidenceRepository,
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


def _baseline(
    *,
    l1: float = 10.0,
    lifecycle_id: str = "life-1",
    created_at: int = 100,
) -> PhaseSettlementBaseline:
    suffix = lifecycle_id.removeprefix("life-")
    return PhaseSettlementBaseline(
        lifecycle_id=lifecycle_id,
        attempt_id=f"attempt-{suffix}",
        entity_l1="sensor.l1",
        entity_l2="sensor.l2",
        entity_l3="sensor.l3",
        baseline_l1_a=l1,
        baseline_l2_a=7.0,
        baseline_l3_a=5.0,
        observed_l1_at=90.0,
        observed_l2_at=91.0,
        observed_l3_at=92.0,
        created_at=created_at,
    ).validated()


@pytest.mark.asyncio
async def test_baseline_is_durable_and_exact_replay_is_idempotent() -> None:
    store = _Store()
    repo = PhaseSettlementEvidenceRepository(store)
    baseline = _baseline()

    first, created = await repo.async_put(baseline)
    replay, replay_created = await repo.async_put(baseline)

    assert first == baseline
    assert replay == baseline
    assert created is True
    assert replay_created is False
    assert store.saves == 1

    reconstructed = PhaseSettlementEvidenceRepository(store)
    assert await reconstructed.async_get("life-1") == baseline


@pytest.mark.asyncio
async def test_changed_binding_is_rejected_fail_closed() -> None:
    repo = PhaseSettlementEvidenceRepository(_Store())
    await repo.async_put(_baseline())

    with pytest.raises(PhaseSettlementEvidenceError, match="binding mismatch"):
        await repo.async_put(_baseline(l1=11.0))


@pytest.mark.asyncio
async def test_failed_save_does_not_publish_evidence_in_memory() -> None:
    store = _Store()
    repo = PhaseSettlementEvidenceRepository(store)
    store.fail = True

    with pytest.raises(RuntimeError, match="store unavailable"):
        await repo.async_put(_baseline())

    store.fail = False
    assert await repo.async_get("life-1") is None


@pytest.mark.asyncio
async def test_prune_preserves_all_active_and_only_newest_inactive_history() -> None:
    store = _Store()
    repo = PhaseSettlementEvidenceRepository(store)
    for index in range(1, 7):
        await repo.async_put(
            _baseline(lifecycle_id=f"life-{index}", created_at=100 + index)
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

    reconstructed = PhaseSettlementEvidenceRepository(store)
    assert await reconstructed.async_get("life-1") is not None
    assert await reconstructed.async_get("life-2") is not None
    assert await reconstructed.async_get("life-3") is None
    assert await reconstructed.async_get("life-6") is not None


@pytest.mark.asyncio
async def test_failed_prune_save_keeps_live_and_durable_history_unchanged() -> None:
    store = _Store()
    repo = PhaseSettlementEvidenceRepository(store)
    for index in range(1, 4):
        await repo.async_put(
            _baseline(lifecycle_id=f"life-{index}", created_at=100 + index)
        )

    store.fail = True
    with pytest.raises(RuntimeError, match="store unavailable"):
        await repo.async_prune(active_lifecycle_ids={"life-1"}, max_inactive=1)
    store.fail = False

    assert await repo.async_get("life-1") is not None
    assert await repo.async_get("life-2") is not None
    assert await repo.async_get("life-3") is not None
    reconstructed = PhaseSettlementEvidenceRepository(store)
    assert await reconstructed.async_get("life-1") is not None
    assert await reconstructed.async_get("life-2") is not None
    assert await reconstructed.async_get("life-3") is not None
