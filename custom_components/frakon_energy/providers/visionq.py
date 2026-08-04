from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp

API_DEVICES_ENDPOINT = "https://app.visionq.cz/api/account_devices.php"
API_MEASUREMENT_ENDPOINT = "https://app.visionq.cz/api/device_last_measurement.php"


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
            battery_state=_to_float(payload.get("battery_state")),
        )

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
