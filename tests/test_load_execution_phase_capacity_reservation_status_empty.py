from __future__ import annotations

import pytest

from custom_components.frakon_energy import (
    load_execution_phase_capacity_reservation_status as status,
)


class _EmptyRepo:
    async def async_snapshot(self, *, now: int):
        return ()


@pytest.mark.asyncio
async def test_phase_reservation_status_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        status,
        "phase_capacity_reservation_repository",
        lambda hass, entry_id: _EmptyRepo(),
    )

    result = await status.async_phase_capacity_reservation_status(
        object(),  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert result["active_count"] == 0
    assert result["reserved_current_a"] == {"L1": 0.0, "L2": 0.0, "L3": 0.0}
    assert result["next_expiry_at"] is None
    assert result["reservations"] == []
