from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
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
        """Return a normalized HDO snapshot from live state and schedule data.

        The configured schedule entity remains the primary authority.  If that
        entity is stale, empty, or accidentally points back to FRAKON Energy,
        recover only from the matching upstream ``sensor.cez_hdo_schedule_*``
        entity.  No timetable is fabricated when the source is unavailable.
        """

        now = dt_util.as_local(now or dt_util.now())
        schedule_state, intervals = self._resolve_schedule()
        live_active = self._read_binary(self.source.low_tariff_entity_id)

        current = next(
            (item for item in intervals if item["start"] <= now < item["end"]),
            None,
        )
        upcoming = next((item for item in intervals if item["start"] > now), None)

        if current is not None:
            active = current["tariff"] == "NT"
            tariff = current["tariff"]
        elif live_active is not None:
            active = live_active
            tariff = "NT" if live_active else "VT"
        else:
            active = None
            tariff = "?"

        next_switch = current["end"] if current is not None else (
            upcoming["start"] if upcoming is not None else None
        )
        countdown = (
            max(0, int((next_switch - now).total_seconds()))
            if next_switch is not None
            else None
        )

        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        today = tuple(
            {
                "start": max(item["start"], day_start).isoformat(),
                "end": min(item["end"], day_end).isoformat(),
                "tariff": item["tariff"],
            }
            for item in intervals
            if item["end"] > day_start and item["start"] < day_end
        )

        source_available = any(
            self._state_available(entity_id)
            for entity_id in (
                self.source.schedule_entity_id,
                self.source.low_tariff_entity_id,
                self.source.current_price_entity_id,
                self.source.data_valid_entity_id,
            )
        ) or schedule_state is not None

        return CezHdoSnapshot(
            low_tariff_active=active,
            tariff=tariff,
            interval_start=current["start"] if current else None,
            interval_end=current["end"] if current else None,
            next_switch=next_switch,
            countdown_seconds=countdown,
            source_available=source_available,
            data_valid=self._read_binary(self.source.data_valid_entity_id),
            current_price=self._read_float(self.source.current_price_entity_id),
            today_schedule=today,
        )

    def _resolve_schedule(self) -> tuple[Any | None, list[dict[str, Any]]]:
        """Resolve a structured ČEZ schedule without crossing source identities."""

        configured = self._hass.states.get(self.source.schedule_entity_id)
        configured_intervals = self._parse_schedule_state(configured)
        if configured_intervals:
            return configured, configured_intervals

        for entity_id in self._derived_schedule_entity_ids():
            state = self._hass.states.get(entity_id)
            parsed = self._parse_schedule_state(state)
            if parsed:
                return state, parsed

        matches: list[tuple[Any, list[dict[str, Any]]]] = []
        async_all = getattr(self._hass.states, "async_all", None)
        if callable(async_all):
            for state in async_all("sensor"):
                entity_id = str(getattr(state, "entity_id", ""))
                if not entity_id.startswith("sensor.cez_hdo_schedule_"):
                    continue
                parsed = self._parse_schedule_state(state)
                if parsed:
                    matches.append((state, parsed))

        if len(matches) == 1:
            return matches[0]
        return configured, []

    def _derived_schedule_entity_ids(self) -> tuple[str, ...]:
        """Derive only exact upstream schedule IDs from already-bound siblings."""

        candidates: list[str] = []
        sibling_patterns = (
            (self.source.low_tariff_entity_id, "cez_hdo_lowtariffactive_"),
            (self.source.current_price_entity_id, "cez_hdo_currentprice_"),
            (self.source.data_valid_entity_id, "cez_hdo_data_valid_"),
        )
        for entity_id, marker in sibling_patterns:
            suffix = self._suffix_after_marker(entity_id, marker)
            if not suffix:
                continue
            candidate = f"sensor.cez_hdo_schedule_{suffix}"
            if candidate not in candidates:
                candidates.append(candidate)
        return tuple(candidates)

    @staticmethod
    def _suffix_after_marker(entity_id: str | None, marker: str) -> str | None:
        if not entity_id:
            return None
        object_id = entity_id.split(".", 1)[-1]
        if marker not in object_id:
            return None
        suffix = object_id.split(marker, 1)[1].strip()
        return suffix or None

    def _parse_schedule_state(self, state: Any | None) -> list[dict[str, Any]]:
        if state is None or getattr(state, "state", None) in {"unknown", "unavailable"}:
            return []
        attributes = getattr(state, "attributes", {}) or {}
        for key in ("schedule", "today_schedule", "dnesni_rozvrh", "intervals"):
            parsed = self._parse_schedule(attributes.get(key))
            if parsed:
                return parsed
        return []

    def _parse_schedule(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []

        parsed: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            start = dt_util.parse_datetime(str(item.get("start", item.get("from", ""))))
            end = dt_util.parse_datetime(str(item.get("end", item.get("to", ""))))
            tariff = str(item.get("tariff", item.get("rate", ""))).upper()
            if start is None or end is None or tariff not in {"NT", "VT"}:
                continue
            start_local = dt_util.as_local(start)
            end_local = dt_util.as_local(end)
            if end_local <= start_local:
                continue
            parsed.append(
                {
                    "start": start_local,
                    "end": end_local,
                    "tariff": tariff,
                }
            )

        return sorted(parsed, key=lambda item: item["start"])

    def _state_available(self, entity_id: str | None) -> bool:
        if not entity_id:
            return False
        state = self._hass.states.get(entity_id)
        return state is not None and state.state not in {"unknown", "unavailable"}

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