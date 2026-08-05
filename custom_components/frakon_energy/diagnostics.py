from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

TO_REDACT = {
    "password",
    "username",
    "token",
    "api_key",
    "consumer_key",
    "consumer_secret",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return safe diagnostics for one FRAKON Energy config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    data = getattr(coordinator, "data", None)

    diagnostics: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": {
            "name": getattr(coordinator, "name", None),
            "last_update_success": getattr(coordinator, "last_update_success", None),
            "last_exception": str(getattr(coordinator, "last_exception", "") or ""),
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if getattr(coordinator, "update_interval", None)
                else None
            ),
        },
        "data": _serialize_data(data),
    }

    history = getattr(coordinator, "history", None)
    if history is not None:
        snapshots = history.snapshots()
        daily = history.daily_consumption()
        diagnostics["history"] = {
            "snapshot_count": len(snapshots),
            "daily_consumption_count": len(daily),
            "first_snapshot": snapshots[0].captured_at.isoformat() if snapshots else None,
            "last_snapshot": snapshots[-1].captured_at.isoformat() if snapshots else None,
            "latest_timestamp": history.latest_timestamp,
        }

    return diagnostics


def _serialize_data(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass
    if isinstance(value, dict):
        return {str(key): _serialize_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize_data(item) for item in value]
    slots = getattr(type(value), "__slots__", ())
    if slots:
        return {
            slot: _serialize_data(getattr(value, slot))
            for slot in slots
            if hasattr(value, slot)
        }
    if hasattr(value, "__dict__"):
        return {
            str(key): _serialize_data(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    return str(value)
