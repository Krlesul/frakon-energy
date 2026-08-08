from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_final_capacity_recheck as final_capacity
from custom_components.frakon_energy.load_execution_final_capacity_recheck import FinalCapacityRecheckError
from custom_components.frakon_energy.load_execution_final_phase_recheck import FinalPhaseRecheckError


@pytest.mark.asyncio
async def test_total_capacity_success_requires_final_phase_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    total = SimpleNamespace(can_start=True, reason="capacity_available")

    async def total_recheck(hass: Any, *, entry_id: str) -> Any:
        return total

    async def phase_recheck(hass: Any, *, entry_id: str) -> object:
        calls.append(entry_id)
        return object()

    monkeypatch.setattr(final_capacity, "async_final_capacity_recheck", total_recheck)
    monkeypatch.setattr(final_capacity, "async_require_final_phase_recheck", phase_recheck)

    result = await final_capacity.async_require_final_capacity_recheck(object(), entry_id="entry-1")

    assert result is total
    assert calls == ["entry-1"]


@pytest.mark.asyncio
async def test_final_phase_block_is_fail_closed_at_capacity_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    total = SimpleNamespace(can_start=True, reason="capacity_available")

    async def total_recheck(hass: Any, *, entry_id: str) -> Any:
        return total

    async def phase_recheck(hass: Any, *, entry_id: str) -> object:
        raise FinalPhaseRecheckError("projected_phase_limit_exceeded")

    monkeypatch.setattr(final_capacity, "async_final_capacity_recheck", total_recheck)
    monkeypatch.setattr(final_capacity, "async_require_final_phase_recheck", phase_recheck)

    with pytest.raises(FinalCapacityRecheckError, match="projected_phase_limit_exceeded"):
        await final_capacity.async_require_final_capacity_recheck(object(), entry_id="entry-1")


@pytest.mark.asyncio
async def test_total_capacity_block_short_circuits_phase_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    total = SimpleNamespace(can_start=False, reason="site_capacity_headroom_insufficient")

    async def total_recheck(hass: Any, *, entry_id: str) -> Any:
        return total

    async def phase_recheck(hass: Any, *, entry_id: str) -> object:
        calls.append(entry_id)
        return object()

    monkeypatch.setattr(final_capacity, "async_final_capacity_recheck", total_recheck)
    monkeypatch.setattr(final_capacity, "async_require_final_phase_recheck", phase_recheck)

    with pytest.raises(FinalCapacityRecheckError, match="headroom_insufficient"):
        await final_capacity.async_require_final_capacity_recheck(object(), entry_id="entry-1")

    assert calls == []
