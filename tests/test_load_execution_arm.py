from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_arm as arm
from custom_components.frakon_energy.load_execution_arm import (
    ExecutionArmError,
    ExecutionArmRepository,
    ExecutionDisarmedError,
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
            raise RuntimeError("arm store unavailable")
        self.data = data


class _Hass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}


@pytest.mark.asyncio
async def test_first_use_is_persistently_fail_closed_disarmed() -> None:
    store = _Store()
    repo = ExecutionArmRepository(store)

    state = await repo.async_get()

    assert state.armed is False
    assert state.revision == 0
    assert state.changed_at == 0
    assert state.changed_by is None
    assert store.saves == 0


@pytest.mark.asyncio
async def test_arm_survives_repository_reconstruction_and_disarm_is_persistent() -> None:
    store = _Store()
    first = ExecutionArmRepository(store)

    armed = await first.async_set(
        armed=True,
        changed_at=100,
        changed_by="admin-1",
    )
    replay = await first.async_set(
        armed=True,
        changed_at=101,
        changed_by="admin-2",
    )

    assert armed.changed is True
    assert armed.state.armed is True
    assert armed.state.revision == 1
    assert armed.state.changed_by == "admin-1"
    assert replay.changed is False
    assert replay.state == armed.state
    assert store.saves == 1

    reconstructed = ExecutionArmRepository(store)
    loaded = await reconstructed.async_get()
    assert loaded == armed.state

    disarmed = await reconstructed.async_set(
        armed=False,
        changed_at=200,
        changed_by="admin-2",
    )
    assert disarmed.changed is True
    assert disarmed.state.armed is False
    assert disarmed.state.revision == 2
    assert store.saves == 2

    final = await ExecutionArmRepository(store).async_get()
    assert final == disarmed.state


@pytest.mark.asyncio
async def test_failed_arm_save_rolls_back_in_memory_state() -> None:
    store = _Store()
    repo = ExecutionArmRepository(store)
    assert (await repo.async_get()).armed is False
    store.fail = True

    with pytest.raises(RuntimeError, match="arm store unavailable"):
        await repo.async_set(
            armed=True,
            changed_at=100,
            changed_by="admin-1",
        )

    assert (await repo.async_get()).armed is False
    assert (await repo.async_get()).revision == 0


@pytest.mark.asyncio
async def test_non_boolean_persisted_arm_value_is_rejected_fail_closed() -> None:
    store = _Store()
    store.data = {
        "schema_version": 1,
        "state": {
            "armed": "false",
            "revision": 1,
            "changed_at": 100,
            "changed_by": "admin-1",
        },
    }
    repo = ExecutionArmRepository(store)

    with pytest.raises(ExecutionArmError, match="armed must be boolean"):
        await repo.async_get()


@pytest.mark.asyncio
async def test_non_boolean_arm_mutation_is_rejected() -> None:
    repo = ExecutionArmRepository(_Store())

    with pytest.raises(ExecutionArmError, match="armed must be boolean"):
        await repo.async_set(
            armed="true",  # type: ignore[arg-type]
            changed_at=100,
            changed_by="admin-1",
        )


@pytest.mark.asyncio
async def test_require_execution_armed_rejects_disarmed_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = ExecutionArmRepository(_Store())
    monkeypatch.setattr(arm, "execution_arm_repository", lambda hass, entry_id: repo)

    with pytest.raises(ExecutionDisarmedError, match="DISARMED"):
        await arm.async_require_execution_armed(_Hass(), "entry-1")  # type: ignore[arg-type]

    await repo.async_set(armed=True, changed_at=100, changed_by="admin")
    state = await arm.async_require_execution_armed(_Hass(), "entry-1")  # type: ignore[arg-type]
    assert state.armed is True


@pytest.mark.asyncio
async def test_arm_guard_serializes_disarm_after_inflight_physical_boundary() -> None:
    hass = _Hass()
    entered = asyncio.Event()
    release = asyncio.Event()
    order: list[str] = []

    async def physical_boundary() -> None:
        async with arm.execution_arm_guard(hass, "entry-1"):  # type: ignore[arg-type]
            order.append("call-enter")
            entered.set()
            await release.wait()
            order.append("call-exit")

    async def disarm_change() -> None:
        await entered.wait()
        async with arm.execution_arm_guard(hass, "entry-1"):  # type: ignore[arg-type]
            order.append("disarm")

    first = asyncio.create_task(physical_boundary())
    second = asyncio.create_task(disarm_change())
    await entered.wait()
    await asyncio.sleep(0)
    assert order == ["call-enter"]
    release.set()
    await asyncio.gather(first, second)

    assert order == ["call-enter", "call-exit", "disarm"]
