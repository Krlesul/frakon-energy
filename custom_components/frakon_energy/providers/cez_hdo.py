from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util


@dataclass(frozen=True, slots=True)
class CezHdoSnapshot:
    """Normalized state derived from the existing ČEZ HDO integration."""

    low_tariff_active: bool | None
    tariff: str
    next_switch: datetime | None
    countdown_seconds: int | None
    source_available: bool


class CezHdoAdapter:
    """Read existing ČEZ HDO entities without duplicating source polling."""

    def __init__(
        self,
        hass: HomeAssistant,
        low_tariff_entity_id: str,
        low_tariff_start_entity_id: str | None = None,
        low_tariff_end_entity_id: str | None = None,
    ) -> None:
        self._hass = hass
        self.low_tariff_entity_id = low_tariff_entity_id
        self.low_tariff_start_entity_id = low_tariff_start_entity_id
        self.low_tariff_end_entity_id = low_tariff_end_entity_id

    def snapshot(self, now: datetime | None = None) -> CezHdoSnapshot:
        """Return a normalized HDO snapshot from current Home Assistant states."""
        now = now or dt_util.now()
        source = self._hass.states.get(self.low_tariff_entity_id)

        if source is None or source.state in {"unknown", "unavailable"}:
            return CezHdoSnapshot(
                low_tariff_active=None,
                tariff="?",
                next_switch=None,
                countdown_seconds=None,
                source_available=False,
            )

        active = source.state == "on"
        next_entity_id = (
            self.low_tariff_end_entity_id if active else self.low_tariff_start_entity_id
        )
        next_switch = self._read_time_entity(next_entity_id, now)
        countdown = None
        if next_switch is not None:
            countdown = max(0, int((next_switch - now).total_seconds()))

        return CezHdoSnapshot(
            low_tariff_active=active,
            tariff="NT" if active else "VT",
            next_switch=next_switch,
            countdown_seconds=countdown,
            source_available=True,
        )

    def _read_time_entity(
        self, entity_id: str | None, now: datetime
    ) -> datetime | None:
        if not entity_id:
            return None

        state = self._hass.states.get(entity_id)
        if state is None or state.state in {"unknown", "unavailable", ""}:
            return None

        raw = state.state.strip()

        parsed = dt_util.parse_datetime(raw)
        if parsed is not None:
            parsed = dt_util.as_local(parsed)
            if parsed <= now:
                parsed = parsed.replace(day=parsed.day) + dt_util.dt.timedelta(days=1)
            return parsed

        parsed_time = dt_util.parse_time(raw)
        if parsed_time is None:
            return None

        candidate = now.replace(
            hour=parsed_time.hour,
            minute=parsed_time.minute,
            second=parsed_time.second,
            microsecond=0,
        )
        if candidate <= now:
            candidate += dt_util.dt.timedelta(days=1)
        return candidate
