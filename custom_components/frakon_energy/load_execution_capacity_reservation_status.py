"""Read-only diagnostics for durable Site Capacity reservations."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.core import HomeAssistant

from .load_execution_capacity_reservation import capacity_reservation_repository


async def async_capacity_reservation_status(
    hass: HomeAssistant,
    *,
    entry_id: str,
    now: int | None = None,
) -> dict[str, Any]:
    """Return active capacity reservations without mutating execution state."""
    if not entry_id:
        raise ValueError("entry_id is required")
    now_ts = int(time.time()) if now is None else int(now)
    if now_ts <= 0:
        raise ValueError("now must be positive")

    reservations = await capacity_reservation_repository(hass, entry_id).async_active(now=now_ts)
    total_reserved_kw = sum(item.power_kw for item in reservations)
    nearest_expiry = min((item.expires_at for item in reservations), default=None)
    return {
        "entry_id": entry_id,
        "active_count": len(reservations),
        "total_reserved_kw": total_reserved_kw,
        "nearest_expiry": nearest_expiry,
        "seconds_until_nearest_expiry": (
            max(0, nearest_expiry - now_ts) if nearest_expiry is not None else None
        ),
        "reservations": [item.as_dict() for item in reservations],
        "read_only": True,
        "state_transition_performed": False,
        "service_call_performed": False,
        "execution_performed": False,
    }
