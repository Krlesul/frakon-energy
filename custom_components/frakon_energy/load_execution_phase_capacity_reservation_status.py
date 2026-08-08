"""Read-only observability for durable per-phase current reservations."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_phase_capacity_reservation import (
    phase_capacity_reservation_repository,
)
from .site_phase_capacity import build_site_phase_capacity_status

_PHASES = ("L1", "L2", "L3")


def _empty_phase_values() -> dict[str, None]:
    return {phase: None for phase in _PHASES}


def _effective_capacity(
    hass: HomeAssistant,
    *,
    entry_id: str,
    reserved_current_a: dict[str, float],
) -> tuple[bool, str | None, dict[str, float | None], dict[str, float | None], dict[str, float | None]]:
    """Return reservation-adjusted live current/headroom without mutating state."""
    try:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ValueError("FRAKON Energy config entry not found")
        capacity = build_site_phase_capacity_status(
            hass,
            entry_id=entry_id,
            options=entry.options,
        )
    except Exception as err:
        empty = _empty_phase_values()
        return False, str(err), dict(empty), dict(empty), dict(empty)

    effective_current: dict[str, float | None] = {}
    effective_headroom: dict[str, float | None] = {}
    effective_over_limit: dict[str, float | None] = {}
    for phase in _PHASES:
        source = capacity.phases.get(phase)
        if (
            not capacity.configured
            or not capacity.source_ready
            or source is None
            or source.current_a is None
            or source.max_current_a is None
        ):
            effective_current[phase] = None
            effective_headroom[phase] = None
            effective_over_limit[phase] = None
            continue
        current = source.current_a + reserved_current_a[phase]
        limit = source.max_current_a
        effective_current[phase] = current
        effective_headroom[phase] = max(0.0, limit - current)
        effective_over_limit[phase] = max(0.0, current - limit)

    return True, None, effective_current, effective_headroom, effective_over_limit


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
            "reserved_current_a": _empty_phase_values(),
            "next_expiry_at": None,
            "reservations": [],
            "capacity_healthy": False,
            "capacity_error": "phase reservation storage unavailable",
            "effective_current_a": _empty_phase_values(),
            "effective_headroom_a": _empty_phase_values(),
            "effective_over_limit_a": _empty_phase_values(),
            "read_only": True,
            "state_transition_performed": False,
            "service_call_performed": False,
            "execution_performed": False,
        }

    totals = {phase: 0.0 for phase in _PHASES}
    for item in reservations:
        currents = item.currents()
        for phase in _PHASES:
            totals[phase] += currents[phase]

    capacity_healthy, capacity_error, effective_current, effective_headroom, effective_over = (
        _effective_capacity(
            hass,
            entry_id=entry_id,
            reserved_current_a=totals,
        )
    )
    next_expiry = min((item.expires_at for item in reservations), default=None)
    return {
        "storage_healthy": True,
        "last_error": None,
        "active_count": len(reservations),
        "reserved_current_a": totals,
        "next_expiry_at": next_expiry,
        "reservations": [item.as_dict() for item in reservations],
        "capacity_healthy": capacity_healthy,
        "capacity_error": capacity_error,
        "effective_current_a": effective_current,
        "effective_headroom_a": effective_headroom,
        "effective_over_limit_a": effective_over,
        "read_only": True,
        "state_transition_performed": False,
        "service_call_performed": False,
        "execution_performed": False,
    }
