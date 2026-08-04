from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
from .providers.visionq import VisionQApiClient, VisionQAuthError, VisionQConnectionError, VisionQMeasurement

_LOGGER = logging.getLogger(__name__)


class FrakonEnergyCoordinator(DataUpdateCoordinator[VisionQMeasurement]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: VisionQApiClient) -> None:
        self.entry = entry
        self.client = client
        self.eui = entry.data["eui"]
        interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(hass, _LOGGER, name=f"FRAKON Energy {self.eui}", update_interval=timedelta(seconds=interval), config_entry=entry)

    async def _async_update_data(self) -> VisionQMeasurement:
        try:
            return await self.client.async_get_measurement(self.eui)
        except VisionQAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except VisionQConnectionError as err:
            raise UpdateFailed(str(err)) from err
