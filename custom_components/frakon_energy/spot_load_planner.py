"""Device-independent flexible-load planning for FRAKON Energy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class FlexibleLoadRequest:
    """Describe an energy task that may be shifted in time."""

    name: str
    duration_minutes: int
    power_kw: float
    earliest_start: datetime | None = None
    latest_end: datetime | None = None

    def validated(self) -> "FlexibleLoadRequest":
        if not self.name.strip():
            raise ValueError("load name is required")
        if self.duration_minutes <= 0 or self.duration_minutes % 15 != 0:
            raise ValueError("duration must be a positive multiple of 15 minutes")
        if self.power_kw <= 0:
            raise ValueError("power must be greater than zero")
        if self.earliest_start and self.latest_end and self.earliest_start >= self.latest_end:
            raise ValueError("earliest start must be before latest end")
        return self


def plan_flexible_load(intervals: list[dict[str, Any]], request: FlexibleLoadRequest) -> dict[str, Any] | None:
    """Find the cheapest contiguous slot satisfying a flexible-load request."""
    request.validated()
    count = request.duration_minutes // 15
    if len(intervals) < count:
        return None
    best: dict[str, Any] | None = None
    for index in range(len(intervals) - count + 1):
        window = intervals[index : index + count]
        try:
            starts_at = datetime.fromisoformat(str(window[0]["starts_at"]))
            ends_at = datetime.fromisoformat(str(window[-1]["ends_at"]))
            prices = [float(item["price_czk_kwh"]) for item in window]
        except (KeyError, TypeError, ValueError):
            continue
        if request.earliest_start and starts_at < request.earliest_start:
            continue
        if request.latest_end and ends_at > request.latest_end:
            continue
        energy_kwh = request.power_kw * request.duration_minutes / 60
        average_price = sum(prices) / len(prices)
        candidate = {
            "load": asdict(request),
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "interval_count": count,
            "energy_kwh": energy_kwh,
            "average_czk_kwh": average_price,
            "estimated_energy_cost_czk": energy_kwh * average_price,
            "minimum_czk_kwh": min(prices),
            "maximum_czk_kwh": max(prices),
        }
        if best is None or candidate["estimated_energy_cost_czk"] < best["estimated_energy_cost_czk"]:
            best = candidate
    return best
