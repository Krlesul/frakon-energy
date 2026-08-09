from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.frakon_energy import load_execution_phase_settlement_runtime as runtime_module
from custom_components.frakon_energy.load_execution_phase_capacity_reservation import (
    PhaseCapacityReservation,
)


class _ReservationRepo:
    def __init__(self, reservations) -> None:
        self.reservations = reservations

    async def async_snapshot(self, *, now: int):
        return self.reservations


class _Hass:
    data = {}


class _TaskHass:
    def __init__(self) -> None:
        self.data: dict = {}

    def async_create_task(self, coro):
        return asyncio.create_task(coro)


def _reservation() -> PhaseCapacityReservation:
    return PhaseCapacityReservation(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        current_l1_a=8.0,
        current_l2_a=0.0,
        current_l3_a=0.0,
        created_at=100,
        expires_at=9999999999,
    ).validated()


@pytest.mark.asyncio
async def test_runtime_waits_until_confirmation_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _ReservationRepo((_reservation(),))
    monkeypatch.setattr(runtime_module, "phase_capacity_reservation_repository", lambda hass, entry_id: repo)

    async def observe(hass, *, entry_id: str, lifecycle_id: str):
        return SimpleNamespace(confirmed=False, status="first_observation_recorded")

    release_calls = 0

    async def release(hass, *, entry_id: str, lifecycle_id: str):
        nonlocal release_calls
        release_calls += 1
        raise AssertionError("release must not run before confirmation")

    monkeypatch.setattr(runtime_module, "async_observe_phase_settlement_confirmation", observe)
    monkeypatch.setattr(runtime_module, "async_release_confirmed_phase_reservation", release)

    runtime = runtime_module.PhaseSettlementRuntime(_Hass(), "entry-1")  # type: ignore[arg-type]
    await runtime.async_process_once()

    status = runtime.statuses()[0]
    assert status.status == runtime_module.STATUS_WAITING
    assert status.confirmation_status == "first_observation_recorded"
    assert release_calls == 0


@pytest.mark.asyncio
async def test_runtime_releases_only_after_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _ReservationRepo((_reservation(),))
    monkeypatch.setattr(runtime_module, "phase_capacity_reservation_repository", lambda hass, entry_id: repo)

    async def observe(hass, *, entry_id: str, lifecycle_id: str):
        return SimpleNamespace(confirmed=True, status="confirmed")

    async def release(hass, *, entry_id: str, lifecycle_id: str):
        return SimpleNamespace(released=True, status="released")

    monkeypatch.setattr(runtime_module, "async_observe_phase_settlement_confirmation", observe)
    monkeypatch.setattr(runtime_module, "async_release_confirmed_phase_reservation", release)

    runtime = runtime_module.PhaseSettlementRuntime(_Hass(), "entry-1")  # type: ignore[arg-type]
    await runtime.async_process_once()

    status = runtime.statuses()[0]
    assert status.status == runtime_module.STATUS_RELEASED
    assert status.confirmation_status == "confirmed"
    assert status.release_status == "released"
    assert status.service_call_performed is False
    assert status.execution_performed is False


@pytest.mark.asyncio
async def test_runtime_error_is_local_and_keeps_processing_contract_fail_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _ReservationRepo((_reservation(),))
    monkeypatch.setattr(runtime_module, "phase_capacity_reservation_repository", lambda hass, entry_id: repo)

    async def observe(hass, *, entry_id: str, lifecycle_id: str):
        raise RuntimeError("telemetry unavailable")

    monkeypatch.setattr(runtime_module, "async_observe_phase_settlement_confirmation", observe)

    runtime = runtime_module.PhaseSettlementRuntime(_Hass(), "entry-1")  # type: ignore[arg-type]
    await runtime.async_process_once()

    status = runtime.statuses()[0]
    assert status.status == runtime_module.STATUS_ERROR
    assert status.last_error == "telemetry unavailable"


@pytest.mark.asyncio
async def test_runtime_prunes_inactive_and_expired_released_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _ReservationRepo(())
    monkeypatch.setattr(runtime_module, "phase_capacity_reservation_repository", lambda hass, entry_id: repo)
    monkeypatch.setattr(runtime_module.time, "time", lambda: 5000)

    runtime = runtime_module.PhaseSettlementRuntime(_Hass(), "entry-1")  # type: ignore[arg-type]
    runtime._status_by_lifecycle = {
        "released-old": runtime_module.PhaseSettlementRuntimeStatus(
            lifecycle_id="released-old",
            status=runtime_module.STATUS_RELEASED,
            last_checked_at=100,
        ),
        "released-recent": runtime_module.PhaseSettlementRuntimeStatus(
            lifecycle_id="released-recent",
            status=runtime_module.STATUS_RELEASED,
            last_checked_at=4900,
        ),
        "waiting-gone": runtime_module.PhaseSettlementRuntimeStatus(
            lifecycle_id="waiting-gone",
            status=runtime_module.STATUS_WAITING,
            last_checked_at=4990,
        ),
    }

    await runtime.async_process_once()

    statuses = {item.lifecycle_id: item for item in runtime.statuses()}
    assert set(statuses) == {"released-recent"}


@pytest.mark.asyncio
async def test_runtime_caps_recent_released_history(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _ReservationRepo(())
    monkeypatch.setattr(runtime_module, "phase_capacity_reservation_repository", lambda hass, entry_id: repo)
    monkeypatch.setattr(runtime_module.time, "time", lambda: 10000)

    runtime = runtime_module.PhaseSettlementRuntime(_Hass(), "entry-1")  # type: ignore[arg-type]
    total = runtime_module.MAX_RETAINED_RELEASED_STATUSES + 5
    runtime._status_by_lifecycle = {
        f"released-{index:03d}": runtime_module.PhaseSettlementRuntimeStatus(
            lifecycle_id=f"released-{index:03d}",
            status=runtime_module.STATUS_RELEASED,
            last_checked_at=9900 + index,
        )
        for index in range(total)
    }

    await runtime.async_process_once()

    statuses = runtime.statuses()
    assert len(statuses) == runtime_module.MAX_RETAINED_RELEASED_STATUSES
    ids = {item.lifecycle_id for item in statuses}
    assert "released-000" not in ids
    assert f"released-{total - 1:03d}" in ids


@pytest.mark.asyncio
async def test_runtime_start_is_idempotent_and_stop_cancels_single_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _TaskHass()
    runtime = runtime_module.PhaseSettlementRuntime(hass, "entry-1")  # type: ignore[arg-type]
    processed = asyncio.Event()

    async def process_once() -> None:
        processed.set()

    monkeypatch.setattr(runtime, "async_process_once", process_once)

    await runtime.async_start()
    await asyncio.wait_for(processed.wait(), timeout=1)
    first_task = runtime._task
    assert first_task is not None
    assert runtime.started is True

    await runtime.async_start()
    assert runtime._task is first_task

    await runtime.async_stop()
    assert runtime.started is False
    assert runtime._task is None
    assert first_task.cancelled() is True


@pytest.mark.asyncio
async def test_runtime_registry_removes_instance_on_stop_and_reload_gets_one_new_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _TaskHass()
    repo = _ReservationRepo(())
    monkeypatch.setattr(runtime_module, "phase_capacity_reservation_repository", lambda hass, entry_id: repo)

    first = runtime_module.phase_settlement_runtime(hass, "entry-1")  # type: ignore[arg-type]
    assert runtime_module.phase_settlement_runtime(hass, "entry-1") is first  # type: ignore[arg-type]

    await runtime_module.async_start_phase_settlement_runtime(hass, "entry-1")  # type: ignore[arg-type]
    assert first.started is True
    await runtime_module.async_stop_phase_settlement_runtime(hass, "entry-1")  # type: ignore[arg-type]
    assert first.started is False

    second = runtime_module.phase_settlement_runtime(hass, "entry-1")  # type: ignore[arg-type]
    assert second is not first
    assert runtime_module.phase_settlement_runtime(hass, "entry-1") is second  # type: ignore[arg-type]
