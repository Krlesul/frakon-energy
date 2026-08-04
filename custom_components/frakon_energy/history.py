from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .providers.visionq import VisionQMeasurement

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = "frakon_energy.visionq_history"


@dataclass(frozen=True, slots=True)
class MeterSnapshot:
    captured_at: datetime
    high_rate_kwh: Decimal
    low_rate_kwh: Decimal


@dataclass(frozen=True, slots=True)
class DailyConsumption:
    day: date
    high_rate_kwh: Decimal
    low_rate_kwh: Decimal


class VisionQHistoryStore:
    """Persist daily cumulative VisionQ readings independently of HA Recorder.

    One canonical snapshot is retained for each local calendar day. The store is
    intentionally small and survives Recorder purges, restarts and dashboard
    changes. Official provider history can later be merged into this store.
    """

    def __init__(self, hass: HomeAssistant, eui: str) -> None:
        self._store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.{eui}",
        )
        self._snapshots: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        payload = await self._store.async_load() or {}
        snapshots = payload.get("snapshots", {})
        self._snapshots = snapshots if isinstance(snapshots, dict) else {}

    async def async_record(self, measurement: VisionQMeasurement) -> bool:
        captured = datetime.fromtimestamp(measurement.timestamp).astimezone()
        key = captured.date().isoformat()
        new_value = {
            "captured_at": captured.isoformat(),
            "high_rate_kwh": str(measurement.high_rate_kwh),
            "low_rate_kwh": str(measurement.low_rate_kwh),
        }
        if self._snapshots.get(key) == new_value:
            return False
        self._snapshots[key] = new_value
        await self._store.async_save({"snapshots": self._snapshots})
        return True

    def snapshots(self) -> tuple[MeterSnapshot, ...]:
        result: list[MeterSnapshot] = []
        for item in self._snapshots.values():
            try:
                result.append(
                    MeterSnapshot(
                        captured_at=datetime.fromisoformat(item["captured_at"]),
                        high_rate_kwh=Decimal(item["high_rate_kwh"]),
                        low_rate_kwh=Decimal(item["low_rate_kwh"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return tuple(sorted(result, key=lambda item: item.captured_at))

    def daily_consumption(self) -> tuple[DailyConsumption, ...]:
        readings = self.snapshots()
        result: list[DailyConsumption] = []
        for previous, current in zip(readings, readings[1:]):
            high = current.high_rate_kwh - previous.high_rate_kwh
            low = current.low_rate_kwh - previous.low_rate_kwh
            if high < 0 or low < 0:
                continue
            result.append(
                DailyConsumption(
                    day=current.captured_at.date(),
                    high_rate_kwh=high,
                    low_rate_kwh=low,
                )
            )
        return tuple(result)

    async def async_import_snapshots(self, snapshots: tuple[MeterSnapshot, ...]) -> int:
        """Merge official provider history without overwriting newer local data."""
        imported = 0
        for snapshot in snapshots:
            key = snapshot.captured_at.astimezone().date().isoformat()
            existing = self._snapshots.get(key)
            existing_time = (
                datetime.fromisoformat(existing["captured_at"])
                if existing and existing.get("captured_at")
                else None
            )
            if existing_time is not None and existing_time >= snapshot.captured_at:
                continue
            self._snapshots[key] = {
                "captured_at": snapshot.captured_at.isoformat(),
                "high_rate_kwh": str(snapshot.high_rate_kwh),
                "low_rate_kwh": str(snapshot.low_rate_kwh),
            }
            imported += 1
        if imported:
            await self._store.async_save({"snapshots": self._snapshots})
        return imported
