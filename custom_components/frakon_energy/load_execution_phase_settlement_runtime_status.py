"""Read-only status snapshot for the phase settlement runtime."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_phase_settlement_runtime import PhaseSettlementRuntime, _RUNTIME_KEY


def phase_settlement_runtime_status(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> dict[str, Any]:
    """Return runtime state without creating, starting, stopping, or mutating it."""
    if not entry_id:
        raise ValueError("entry_id is required")
    domain_data = hass.data.get(DOMAIN)
    runtimes = domain_data.get(_RUNTIME_KEY) if isinstance(domain_data, dict) else None
    runtime = runtimes.get(entry_id) if isinstance(runtimes, dict) else None
    if not isinstance(runtime, PhaseSettlementRuntime):
        return {
            "started": False,
            "healthy": False,
            "last_error": None,
            "statuses": [],
            "poll_seconds": 5,
            "read_only": True,
            "service_call_performed": False,
            "execution_performed": False,
        }
    return {
        "started": runtime.started,
        "healthy": runtime.healthy,
        "last_error": runtime.last_error,
        "statuses": [status.as_dict() for status in runtime.statuses()],
        "poll_seconds": 5,
        "read_only": True,
        "service_call_performed": False,
        "execution_performed": False,
    }
