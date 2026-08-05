from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
from .history import MeterSnapshot, VisionQHistoryStore
from .providers.visionq import (
    VisionQApiClient,
    VisionQAuthError,
    VisionQConnectionError,
    VisionQMeasurement,
)

_LOGGER = logging.getLogger(__name__)
_HISTORY_SYNC_INTERVAL = timedelta(hours=24)
_HISTORY_OVERLAP = timedelta(days=1)
_HISTORY_MAX_LOOKBACK = timedelta(days=365)


class FrakonEnergyCoordinator(DataUpdateCoordinator[VisionQMeasurement]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: VisionQApiClient) -> None:
        self.entry = entry
        self.client = client
        self.eui = entry.data["eui"]
        self.history = VisionQHistoryStore(hass, self.eui)
        self._last_history_sync: datetime | None = None
        interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            name=f"FRAKON Energy {self.eui}",
            update_interval=timedelta(seconds=interval),
            config_entry=entry,
        )

    async def async_initialize_history(self) -> None:
        await self.history.async_load()
        await self._async_sync_official_history(force=True)

    async def _async_update_data(self) -> VisionQMeasurement:
        try:
            measurement = await self.client.async_get_measurement(self.eui)
            await self.history.async_record(measurement)
            await self._async_sync_official_history()
            return measurement
        except VisionQAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except VisionQConnectionError as err:
            raise UpdateFailed(str(err)) from err

    async def _async_sync_official_history(self, *, force: bool = False) -> None:
        """Import VisionQ history at most once per day and continue incrementally."""
        now = datetime.now().astimezone()
        if (
            not force
            and self._last_history_sync is not None
            and now - self._last_history_sync < _HISTORY_SYNC_INTERVAL
        ):
            return

        latest = self.history.latest_timestamp
        if latest is None:
            from_timestamp = int((now - _HISTORY_MAX_LOOKBACK).timestamp())
        else:
            from_timestamp = max(
                int((now - _HISTORY_MAX_LOOKBACK).timestamp()),
                latest - int(_HISTORY_OVERLAP.total_seconds()),
            )

        try:
            points = await self.client.async_get_history(self.eui, from_timestamp)
        except VisionQAuthError:
            raise
        except VisionQConnectionError as err:
            _LOGGER.warning("VisionQ history synchronization failed: %s", err)
            return

        snapshots = tuple(
            MeterSnapshot(
                captured_at=datetime.fromtimestamp(point.timestamp).astimezone(),
                high_rate_kwh=Decimal(str(point.high_rate_kwh)),
                low_rate_kwh=Decimal(str(point.low_rate_kwh)),
            )
            for point in points
            if point.high_rate_kwh is not None and point.low_rate_kwh is not None
        )
        imported = await self.history.async_import_snapshots(snapshots)
        self._last_history_sync = now
        _LOGGER.debug(
            "Imported %s VisionQ history snapshots for %s from %s",
            imported,
            self.eui,
            datetime.fromtimestamp(from_timestamp).astimezone().isoformat(),
        )
