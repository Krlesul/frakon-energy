from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_HDO_CURRENT_PRICE_ENTITY,
    CONF_HDO_DATA_VALID_ENTITY,
    CONF_HDO_LOW_TARIFF_ENTITY,
    CONF_HDO_SCHEDULE_ENTITY,
    HDO_UPDATE_INTERVAL,
)
from .providers.cez_hdo import CezHdoAdapter, CezHdoSnapshot


class CezHdoCoordinator(DataUpdateCoordinator[CezHdoSnapshot]):
    """Refresh normalized HDO state from existing Home Assistant entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.adapter = CezHdoAdapter(
            hass,
            schedule_entity_id=entry.data[CONF_HDO_SCHEDULE_ENTITY],
            low_tariff_entity_id=entry.data.get(CONF_HDO_LOW_TARIFF_ENTITY),
            current_price_entity_id=entry.data.get(CONF_HDO_CURRENT_PRICE_ENTITY),
            data_valid_entity_id=entry.data.get(CONF_HDO_DATA_VALID_ENTITY),
        )
        super().__init__(
            hass,
            logger=__import__("logging").getLogger(__name__),
            name="FRAKON Energy ČEZ HDO",
            update_interval=timedelta(seconds=HDO_UPDATE_INTERVAL),
        )

    async def _async_update_data(self) -> CezHdoSnapshot:
        return self.adapter.snapshot()
