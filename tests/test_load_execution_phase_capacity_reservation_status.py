from __future__ import annotations

from typing import Any

import pytest

from custom_components.frakon_energy import (
    load_execution_phase_capacity_reservation_status as status,
)
from custom_components.frakon_energy.load_execution_phase_capacity_reservation import (
    PhaseCapacityReservation,
)


class _Repo:
    def __init__(self, reservations: tuple[PhaseCapacityReservation, ...]) -> None:
        self.reservations = reservations
        self.snapshot_calls = 0

    async def async_snapshot(self, *, now: int) -> tuple[PhaseCapacityReservation, ...]:
        self.snapshot_calls += 1
        assert now > 0
        return self.reservations


@pytest.mark.asyncio
async def test_phase_reservation_status_sums_currents_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _Repo(
        (
            PhaseCapacityReservation(
                lifecycle_id="life-1",
                attempt_id="attempt-1",
                current_l1_a=6.0,
                current_l2_a=2.0,
                current_l3_a=0.0,
                created_at=100,
                expires_at=400,
            ),
            PhaseCapacityReservation(
                lifecycle_id="life-2",
                attempt_id="attempt-2",
                current_l1_a=3.5,
                current_l2_a=0.0,
                current_l3_a=7.0,
                created_at=120,
                expires_at=500,
            ),
        )
    )
    monkeypatch.setattr(
        status,
        "phase_capacity_reservation_repository",
        lambda hass, entry_id: repo,
    )

    result = await status.async_phase_capacity_reservation_status(
        object(),  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert result["storage_healthy"] is True
    assert result["active_count"] == 2
    assert result["reserved_current_a"] == {"L1": 9.5, "L2": 2.0, "L3": 7.0}
    assert result["next_expiry_at"] == 400
    assert result["read_only"] is True
    assert result["state_transition_performed"] is False
    assert result["service_call_performed"] is False
    assert result["execution_performed"] is False
    assert repo.snapshot_calls == 1


@pytest.mark.asyncio
async def test_phase_reservation_status_fails_closed_on_storage_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BrokenRepo:
        async def async_snapshot(self, *, now: int) -> tuple[Any, ...]:
            raise RuntimeError("phase reservation store unavailable")

    monkeypatch.setattr(
        status,
        "phase_capacity_reservation_repository",
        lambda hass, entry_id: _BrokenRepo(),
    )

    result = await status.async_phase_capacity_reservation_status(
        object(),  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert result["storage_healthy"] is False
    assert result["active_count"] is None
    assert result["reserved_current_a"] == {"L1": None, "L2": None, "L3": None}
    assert "unavailable" in result["last_error"]
    assert result["read_only"] is True
