from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_HDO_CURRENT_PRICE_ENTITY,
    CONF_HDO_DATA_VALID_ENTITY,
    CONF_HDO_LOW_TARIFF_ENTITY,
    CONF_HDO_SCHEDULE_ENTITY,
    CONF_HDO_SOURCE_ID,
    EVENT_TARIFF_CHANGED,
    HDO_UPDATE_INTERVAL,
)
from .providers.cez_hdo import CezHdoAdapter, CezHdoSnapshot
from .providers.cez_hdo_discovery import CezHdoSource

_LOGGER = logging.getLogger(__name__)


class CezHdoCoordinator(DataUpdateCoordinator[CezHdoSnapshot]):
    """Refresh normalized HDO state from existing Home Assistant entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        source = CezHdoSource(
            source_id=entry.data[CONF_HDO_SOURCE_ID],
            name=entry.title,
            schedule_entity_id=entry.data[CONF_HDO_SCHEDULE_ENTITY],
            low_tariff_entity_id=entry.data.get(CONF_HDO_LOW_TARIFF_ENTITY),
            current_price_entity_id=entry.data.get(CONF_HDO_CURRENT_PRICE_ENTITY),
            data_valid_entity_id=entry.data.get(CONF_HDO_DATA_VALID_ENTITY),
        )
        self.source = source
        self.adapter = CezHdoAdapter(hass, source)
        self._last_tariff: str | None = None
        super().__init__(
            hass,
            logger=_LOGGER,
            name="FRAKON Energy ČEZ HDO",
            update_interval=timedelta(seconds=HDO_UPDATE_INTERVAL),
        )

    async def _async_update_data(self) -> CezHdoSnapshot:
        snapshot = self.adapter.snapshot()
        self._emit_tariff_changed_event(snapshot)
        return snapshot

    def _emit_tariff_changed_event(self, snapshot: CezHdoSnapshot) -> None:
        """Emit one event only for a real NT/VT transition.

        The first coordinator refresh establishes the baseline and intentionally
        does not announce anything. Unknown or unavailable states are ignored.
        """

        current = snapshot.tariff
        if current not in {"NT", "VT"}:
            return

        previous = self._last_tariff
        self._last_tariff = current
        if previous is None or previous == current:
            return

        changed_at = dt_util.now()
        event_data = {
            "source_id": self.source.source_id,
            "source_name": self.source.name,
            "previous_tariff": previous,
            "new_tariff": current,
            "low_tariff_active": current == "NT",
            "changed_at": changed_at.isoformat(),
            "next_change_at": (
                snapshot.next_switch.isoformat()
                if snapshot.next_switch is not None
                else None
            ),
            "next_change_in_seconds": snapshot.countdown_seconds,
        }
        self.hass.bus.async_fire(EVENT_TARIFF_CHANGED, event_data)
        _LOGGER.info(
            "FRAKON Energy HDO tariff changed from %s to %s for %s",
            previous,
            current,
            self.source.source_id,
        )
