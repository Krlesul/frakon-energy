"""Read-only observability for durable per-phase current reservations."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.core import HomeAssistant

from .load_execution_phase_capacity_reservation import (
    phase_capacity_reservation_repository,
)


async def async_phase_capacity_reservation_status(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> dict[str, Any]:
    """Return active phase reservations without mutating durable storage."""
    if not entry_id:
        raise ValueError("entry_id is required")

    now_ts = int(time.time())
    try:
        reservations = await phase_capacity_reservation_repository(
            hass, entry_id
        ).async_snapshot(now=now_ts)
    except Exception as err:
        return {
            "storage_healthy": False,
            "last_error": str(err),
            "active_count": None,
            "reserved_current_a": {"L1": None, "L2": None, "L3": None},
            "next_expiry_at": None,
            "reservations": [],
            "read_only": True,
            "state_transition_performed": False,
            "service_call_performed": False,
            "execution_performed": False,
        }

    totals = {"L1": 0.0, "L2": 0.0, "L3": 0.0}
    for item in reservations:
        currents = item.currents()
        for phase in totals:
            totals[phase] += currents[phase]

    next_expiry = min((item.expires_at for item in reservations), default=None)
    return {
        "storage_healthy": True,
        "last_error": None,
        "active_count": len(reservations),
        "reserved_current_a": totals,
        "next_expiry_at": next_expiry,
        "reservations": [item.as_dict() for item in reservations],
        "read_only": True,
        "state_transition_performed": False,
        "service_call_performed": False,
        "execution_performed": False,
    }
