from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.frakon_energy import load_execution_phase_settlement_release as release
from custom_components.frakon_energy.load_execution_lifecycle import STATE_VERIFIED
from custom_components.frakon_energy.load_execution_phase_capacity_reservation import (
    PhaseCapacityReservation,
)


class _ConfirmationRepo:
    def __init__(self, confirmation) -> None:
        self.confirmation = confirmation

    async def async_get(self, lifecycle_id: str):
        return self.confirmation


class _LifecycleRepo:
    def __init__(self, records) -> None:
        self.records = records

    async def async_list(self):
        return self.records


class _ReservationRepo:
    def __init__(self, reservation: PhaseCapacityReservation) -> None:
        self.reservation = reservation
        self.release_calls = 0

    async def async_snapshot(self, *, now: int):
        return (self.reservation,)

    async def async_release(self, *, lifecycle_id: str, attempt_id: str):
        self.release_calls += 1
        assert lifecycle_id == self.reservation.lifecycle_id
        assert attempt_id == self.reservation.attempt_id
        return self.reservation, True


def _confirmation():
    value = SimpleNamespace(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        confirmed_at=200,
    )
    value.as_dict = lambda: {
        "lifecycle_id": value.lifecycle_id,
        "attempt_id": value.attempt_id,
        "confirmed_at": value.confirmed_at,
    }
    return value


def _proof(candidate: bool):
    value = SimpleNamespace(candidate=candidate, reason="proof-ready" if candidate else "proof-not-ready")
    value.as_dict = lambda: {"candidate": value.candidate, "reason": value.reason}
    return value


def _reservation() -> PhaseCapacityReservation:
    return PhaseCapacityReservation(
        lifecycle_id="life-1",
        attempt_id="attempt-1",
        current_l1_a=8.0,
        current_l2_a=0.0,
        current_l3_a=0.0,
        created_at=100,
        expires_at=400,
    ).validated()


@pytest.mark.asyncio
async def test_release_requires_fresh_final_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    reservation_repo = _ReservationRepo(_reservation())
    monkeypatch.setattr(release, "phase_settlement_confirmation_repository", lambda hass, entry_id: _ConfirmationRepo(_confirmation()))
    monkeypatch.setattr(
        release,
        "lifecycle_repository",
        lambda hass, entry_id: _LifecycleRepo([SimpleNamespace(lifecycle_id="life-1", attempt_id="attempt-1", state=STATE_VERIFIED)]),
    )
    monkeypatch.setattr(release, "phase_capacity_reservation_repository", lambda hass, entry_id: reservation_repo)

    async def final_proof(hass, *, entry_id: str, lifecycle_id: str):
        return _proof(False)

    monkeypatch.setattr(release, "async_phase_settlement_proof", final_proof)

    result = await release.async_release_confirmed_phase_reservation(
        object(),  # type: ignore[arg-type]
        entry_id="entry-1",
        lifecycle_id="life-1",
    )

    assert result.status == release.STATUS_RECHECK_NOT_READY
    assert result.released is False
    assert reservation_repo.release_calls == 0


@pytest.mark.asyncio
async def test_release_occurs_only_after_confirmation_verified_lifecycle_and_final_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reservation_repo = _ReservationRepo(_reservation())
    monkeypatch.setattr(release, "phase_settlement_confirmation_repository", lambda hass, entry_id: _ConfirmationRepo(_confirmation()))
    monkeypatch.setattr(
        release,
        "lifecycle_repository",
        lambda hass, entry_id: _LifecycleRepo([SimpleNamespace(lifecycle_id="life-1", attempt_id="attempt-1", state=STATE_VERIFIED)]),
    )
    monkeypatch.setattr(release, "phase_capacity_reservation_repository", lambda hass, entry_id: reservation_repo)

    async def final_proof(hass, *, entry_id: str, lifecycle_id: str):
        return _proof(True)

    monkeypatch.setattr(release, "async_phase_settlement_proof", final_proof)

    result = await release.async_release_confirmed_phase_reservation(
        object(),  # type: ignore[arg-type]
        entry_id="entry-1",
        lifecycle_id="life-1",
    )

    assert result.status == release.STATUS_RELEASED
    assert result.released is True
    assert reservation_repo.release_calls == 1
    assert result.released_reservation is not None
