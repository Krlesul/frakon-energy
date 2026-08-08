from __future__ import annotations

from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_capacity_reservation_status as status_module
from custom_components.frakon_energy.load_execution_capacity_reservation import (
    CapacityReservationRepository,
)
from custom_components.frakon_energy.load_execution_capacity_reservation_status import (
    async_capacity_reservation_status,
)


class _Store:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = data


@pytest.mark.asyncio
async def test_status_exposes_active_reservations_total_and_nearest_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = CapacityReservationRepository(_Store())
    await repository.async_reserve(
        lifecycle_id="life-a",
        attempt_id="attempt-a",
        power_kw=11.0,
        now=100,
        ttl_seconds=300,
    )
    await repository.async_reserve(
        lifecycle_id="life-b",
        attempt_id="attempt-b",
        power_kw=3.5,
        now=120,
        ttl_seconds=300,
    )
    monkeypatch.setattr(
        status_module,
        "capacity_reservation_repository",
        lambda hass, entry_id: repository,
    )

    result = await async_capacity_reservation_status(
        object(),  # type: ignore[arg-type]
        entry_id="entry-1",
        now=150,
    )

    assert result["active_count"] == 2
    assert result["total_reserved_kw"] == pytest.approx(14.5)
    assert result["nearest_expiry"] == 400
    assert result["seconds_until_nearest_expiry"] == 250
    assert [item["lifecycle_id"] for item in result["reservations"]] == ["life-a", "life-b"]
    assert result["read_only"] is True
    assert result["state_transition_performed"] is False
    assert result["service_call_performed"] is False
    assert result["execution_performed"] is False


@pytest.mark.asyncio
async def test_status_omits_expired_reservations() -> None:
    repository = CapacityReservationRepository(_Store())
    await repository.async_reserve(
        lifecycle_id="life-a",
        attempt_id="attempt-a",
        power_kw=11.0,
        now=100,
        ttl_seconds=10,
    )
    status_module.capacity_reservation_repository = lambda hass, entry_id: repository

    result = await async_capacity_reservation_status(
        object(),  # type: ignore[arg-type]
        entry_id="entry-1",
        now=111,
    )

    assert result["active_count"] == 0
    assert result["total_reserved_kw"] == pytest.approx(0.0)
    assert result["nearest_expiry"] is None
    assert result["seconds_until_nearest_expiry"] is None
    assert result["reservations"] == []
