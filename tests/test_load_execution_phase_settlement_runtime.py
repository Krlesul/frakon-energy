from __future__ import annotations

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
