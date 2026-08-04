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
    """Persist cumulative VisionQ readings independently of HA Recorder."""

    def __init__(self, hass: HomeAssistant, eui: str) -> None:
        self._store = Store[dict[str, Any]](hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}.{eui}")
        self._snapshots: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        payload = await self._store.async_load() or {}
        snapshots = payload.get("snapshots", {})
        self._snapshots = snapshots if isinstance(snapshots, dict) else {}

    async def async_record(self, measurement: VisionQMeasurement) -> bool:
        if measurement.timestamp is None:
            return False
        captured = datetime.fromtimestamp(measurement.timestamp).astimezone()
        return await self.async_import_snapshots((MeterSnapshot(captured_at=captured, high_rate_kwh=Decimal(str(measurement.high_rate_kwh)), low_rate_kwh=Decimal(str(measurement.low_rate_kwh))),)) > 0

    def snapshots(self) -> tuple[MeterSnapshot, ...]:
        result: list[MeterSnapshot] = []
        for item in self._snapshots.values():
            try:
                result.append(MeterSnapshot(captured_at=datetime.fromisoformat(item["captured_at"]), high_rate_kwh=Decimal(item["high_rate_kwh"]), low_rate_kwh=Decimal(item["low_rate_kwh"])))
            except (KeyError, TypeError, ValueError):
                continue
        return tuple(sorted(result, key=lambda item: item.captured_at))

    @property
    def latest_timestamp(self) -> int | None:
        readings = self.snapshots()
        return int(readings[-1].captured_at.timestamp()) if readings else None

    def daily_consumption(self) -> tuple[DailyConsumption, ...]:
        readings = self.snapshots()
        result: list[DailyConsumption] = []
        for previous, current in zip(readings, readings[1:]):
            high = current.high_rate_kwh - previous.high_rate_kwh
            low = current.low_rate_kwh - previous.low_rate_kwh
            if high < 0 or low < 0:
                continue
            result.append(DailyConsumption(day=current.captured_at.date(), high_rate_kwh=high, low_rate_kwh=low))
        return tuple(result)

    async def async_import_snapshots(self, snapshots: tuple[MeterSnapshot, ...]) -> int:
        imported = 0
        for snapshot in snapshots:
            local_snapshot = snapshot.captured_at.astimezone()
            key = local_snapshot.date().isoformat()
            existing = self._snapshots.get(key)
            existing_time = None
            if existing and existing.get("captured_at"):
                try:
                    existing_time = datetime.fromisoformat(existing["captured_at"])
                except (TypeError, ValueError):
                    existing_time = None
            if existing_time is not None and existing_time >= local_snapshot:
                continue
            self._snapshots[key] = {
                "captured_at": local_snapshot.isoformat(),
                "high_rate_kwh": str(snapshot.high_rate_kwh),
                "low_rate_kwh": str(snapshot.low_rate_kwh),
            }
            imported += 1
        if imported:
            await self._store.async_save({"snapshots": self._snapshots})
        return imported
