from __future__ import annotations

import pytest

from custom_components.frakon_energy.load_execution_phase_capacity_reservation_status import (
    async_phase_capacity_reservation_status,
)


@pytest.mark.asyncio
async def test_phase_reservation_status_requires_entry_id() -> None:
    with pytest.raises(ValueError, match="entry_id is required"):
        await async_phase_capacity_reservation_status(
            object(),  # type: ignore[arg-type]
            entry_id="",
        )
