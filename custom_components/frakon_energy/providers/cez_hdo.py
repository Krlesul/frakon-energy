from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .cez_hdo_discovery import CezHdoSource


@dataclass(frozen=True, slots=True)
class CezHdoSnapshot:
    """Normalized state derived from an existing ČEZ HDO integration."""

    low_tariff_active: bool | None
    tariff: str
    interval_start: datetime | None
    interval_end: datetime | None
    next_switch: datetime | None
    countdown_seconds: int | None
    source_available: bool
    data_valid: bool | None
    current_price: float | None
    today_schedule: tuple[dict[str, Any], ...]


class CezHdoAdapter:
    """Read an existing ČEZ HDO source without duplicating network polling."""

    def __init__(self, hass: HomeAssistant, source: CezHdoSource) -> None:
        self._hass = hass
        self.source = source

    def snapshot(self, now: datetime | None = None) -> CezHdoSnapshot:
        """Return a normalized HDO snapshot from the structured schedule."""

        now = dt_util.as_local(now or dt_util.now())
        schedule_state = self._hass.states.get(self.source.schedule_entity_id)
        if schedule_state is None or schedule_state.state in {"unknown", "unavailable"}:
            return self._unavailable_snapshot()

        intervals = self._parse_schedule(schedule_state.attributes.get("schedule"))
        current = next(
            (item for item in intervals if item["start"] <= now < item["end"]),
            None,
        )
        upcoming = next((item for item in intervals if item["start"] > now), None)

        active = None if current is None else current["tariff"] == "NT"
        tariff = "?" if current is None else current["tariff"]
        next_switch = current["end"] if current is not None else (
            upcoming["start"] if upcoming is not None else None
        )
        countdown = (
            max(0, int((next_switch - now).total_seconds()))
            if next_switch is not None
            else None
        )

        today = tuple(
            {
                "start": item["start"].isoformat(),
                "end": item["end"].isoformat(),
                "tariff": item["tariff"],
            }
            for item in intervals
            if item["start"].date() == now.date()
        )

        return CezHdoSnapshot(
            low_tariff_active=active,
            tariff=tariff,
            interval_start=current["start"] if current else None,
            interval_end=current["end"] if current else None,
            next_switch=next_switch,
            countdown_seconds=countdown,
            source_available=True,
            data_valid=self._read_binary(self.source.data_valid_entity_id),
            current_price=self._read_float(self.source.current_price_entity_id),
            today_schedule=today,
        )

    def _parse_schedule(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []

        parsed: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            start = dt_util.parse_datetime(str(item.get("start", "")))
            end = dt_util.parse_datetime(str(item.get("end", "")))
            tariff = str(item.get("tariff", "")).upper()
            if start is None or end is None or tariff not in {"NT", "VT"}:
                continue
            parsed.append(
                {
                    "start": dt_util.as_local(start),
                    "end": dt_util.as_local(end),
                    "tariff": tariff,
                }
            )

        return sorted(parsed, key=lambda item: item["start"])

    def _read_binary(self, entity_id: str | None) -> bool | None:
        if not entity_id:
            return None
        state = self._hass.states.get(entity_id)
        if state is None or state.state in {"unknown", "unavailable"}:
            return None
        return state.state == "on"

    def _read_float(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        state = self._hass.states.get(entity_id)
        if state is None or state.state in {"unknown", "unavailable"}:
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _unavailable_snapshot(self) -> CezHdoSnapshot:
        return CezHdoSnapshot(
            low_tariff_active=None,
            tariff="?",
            interval_start=None,
            interval_end=None,
            next_switch=None,
            countdown_seconds=None,
            source_available=False,
            data_valid=None,
            current_price=None,
            today_schedule=(),
        )
