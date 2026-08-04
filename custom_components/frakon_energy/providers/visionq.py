from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp

API_DEVICES_ENDPOINT = "https://app.visionq.cz/api/account_devices.php"
API_MEASUREMENT_ENDPOINT = "https://app.visionq.cz/api/device_last_measurement.php"
API_HISTORY_ENDPOINT = "https://api.visionq.cz/device_data.php"


class VisionQError(Exception):
    """Base VisionQ error."""


class VisionQAuthError(VisionQError):
    """Authentication failed."""


class VisionQConnectionError(VisionQError):
    """Connection or response error."""


@dataclass(slots=True)
class VisionQMeasurement:
    eui: str
    high_rate_kwh: float
    low_rate_kwh: float
    timestamp: int | None
    battery_state: float | None

    @property
    def total_kwh(self) -> float:
        return self.high_rate_kwh + self.low_rate_kwh


@dataclass(frozen=True, slots=True)
class VisionQHistoryPoint:
    """One cumulative VisionQ meter reading reconstructed from history metrics."""

    timestamp: int
    high_rate_kwh: float | None
    low_rate_kwh: float | None
    crc: int | None = None


class VisionQApiClient:
    """Async client for the official VisionQ API."""

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._session = session
        self._auth = aiohttp.BasicAuth(username, password)

    async def async_get_devices(self) -> list[dict[str, Any]]:
        payload = await self._async_get_json(API_DEVICES_ENDPOINT)
        devices = payload.get("devices")
        if not isinstance(devices, list):
            raise VisionQConnectionError("VisionQ response does not contain a devices list")
        return devices

    async def async_get_measurement(self, eui: str) -> VisionQMeasurement:
        payload = await self._async_get_json(
            API_MEASUREMENT_ENDPOINT,
            params={"eui": eui},
        )
        return VisionQMeasurement(
            eui=eui,
            high_rate_kwh=float(payload.get("high_rate_kwh", 0)),
            low_rate_kwh=float(payload.get("low_rate_kwh", 0)),
            timestamp=_to_int(payload.get("timestamp")),
            battery_state=_battery_percent(payload.get("battery_state")),
        )

    async def async_get_history(self, eui: str, from_timestamp: int) -> list[VisionQHistoryPoint]:
        """Load official historical readings, limited by VisionQ to one year back.

        VisionQ returns separate rows for metric 1 (VT) and metric 2 (NT).
        Rows with the same timestamp are combined into one cumulative meter point.
        """

        payload = await self._async_get_json(
            API_HISTORY_ENDPOINT,
            params={"eui": eui, "from": str(from_timestamp)},
        )
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise VisionQConnectionError("VisionQ history response does not contain data")

        combined: dict[int, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            timestamp = _to_int(row.get("timestamp"))
            metric = _to_int(row.get("metric"))
            value = _to_float(row.get("value"))
            if timestamp is None or metric not in {1, 2} or value is None:
                continue

            point = combined.setdefault(
                timestamp,
                {"high_rate_kwh": None, "low_rate_kwh": None, "crc": _to_int(row.get("crc"))},
            )
            if metric == 1:
                point["high_rate_kwh"] = value
            else:
                point["low_rate_kwh"] = value

        return [
            VisionQHistoryPoint(
                timestamp=timestamp,
                high_rate_kwh=values["high_rate_kwh"],
                low_rate_kwh=values["low_rate_kwh"],
                crc=values["crc"],
            )
            for timestamp, values in sorted(combined.items())
        ]

    async def _async_get_json(
        self,
        url: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            async with self._session.get(
                url,
                params=params,
                auth=self._auth,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status == 401:
                    raise VisionQAuthError("VisionQ authentication failed")
                if response.status != 200:
                    raise VisionQConnectionError(f"VisionQ returned HTTP {response.status}")
                data = await response.json(content_type=None)
                if not isinstance(data, dict):
                    raise VisionQConnectionError("Unexpected VisionQ response")
                return data
        except VisionQError:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise VisionQConnectionError(str(err)) from err


def _battery_percent(value: Any) -> float | None:
    """Convert VisionQ battery scale 0..254 to Home Assistant percent.

    Value 255 means unknown or externally powered according to VisionQ docs.
    """

    raw = _to_float(value)
    if raw is None or raw < 0 or raw >= 255:
        return None
    return round(raw / 254 * 100, 1)


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
