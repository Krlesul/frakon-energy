"""Flexible-load planning core for FRAKON Energy.

The planner is intentionally device-agnostic. It receives priced market intervals
and a load requirement, then chooses the cheapest eligible contiguous run.
Execution against Home Assistant entities is a separate layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class FlexibleLoadRequest:
    """Describe a flexible load that should run inside a time window."""

    load_id: str
    duration_minutes: int
    earliest_start: datetime | None = None
    latest_end: datetime | None = None
    power_kw: float | None = None

    def validated(self) -> "FlexibleLoadRequest":
        if not self.load_id.strip():
            raise ValueError("load_id is required")
        if self.duration_minutes <= 0:
            raise ValueError("duration_minutes must be positive")
        if self.power_kw is not None and self.power_kw <= 0:
            raise ValueError("power_kw must be positive")
        if self.earliest_start and self.latest_end and self.earliest_start >= self.latest_end:
            raise ValueError("earliest_start must be before latest_end")
        return self


@dataclass(frozen=True, slots=True)
class PlannedLoadWindow:
    """Selected execution window for one flexible load."""

    load_id: str
    starts_at: datetime
    ends_at: datetime
    duration_minutes: int
    average_czk_kwh: float
    minimum_czk_kwh: float
    maximum_czk_kwh: float
    estimated_energy_kwh: float | None
    estimated_cost_czk: float | None
    interval_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "load_id": self.load_id,
            "starts_at": self.starts_at.isoformat(),
            "ends_at": self.ends_at.isoformat(),
            "duration_minutes": self.duration_minutes,
            "average_czk_kwh": self.average_czk_kwh,
            "minimum_czk_kwh": self.minimum_czk_kwh,
            "maximum_czk_kwh": self.maximum_czk_kwh,
            "estimated_energy_kwh": self.estimated_energy_kwh,
            "estimated_cost_czk": self.estimated_cost_czk,
            "interval_count": self.interval_count,
        }


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError("interval timestamp must be an ISO datetime")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _eligible_intervals(
    intervals: list[dict[str, Any]], request: FlexibleLoadRequest
) -> list[dict[str, Any]]:
    eligible: list[dict[str, Any]] = []
    for interval in intervals:
        if interval.get("price_czk_kwh") is None:
            continue
        start = _parse_time(interval.get("starts_at"))
        end = _parse_time(interval.get("ends_at"))
        if end <= start:
            continue
        if request.earliest_start is not None and start < request.earliest_start:
            continue
        if request.latest_end is not None and end > request.latest_end:
            continue
        eligible.append({**interval, "_start": start, "_end": end})
    eligible.sort(key=lambda item: item["_start"])
    return eligible


def plan_contiguous_load(
    intervals: list[dict[str, Any]], request: FlexibleLoadRequest
) -> PlannedLoadWindow | None:
    """Choose the cheapest contiguous period satisfying ``request``.

    Cost comparison is based on time-weighted final customer CZK/kWh. This works
    for 15-minute OTE data and remains correct if interval sizes change later.
    """
    request = request.validated()
    eligible = _eligible_intervals(intervals, request)
    best: PlannedLoadWindow | None = None

    for start_index in range(len(eligible)):
        window: list[dict[str, Any]] = []
        accumulated_minutes = 0.0
        previous_end: datetime | None = None

        for interval in eligible[start_index:]:
            start = interval["_start"]
            end = interval["_end"]
            if previous_end is not None and start != previous_end:
                break
            minutes = (end - start).total_seconds() / 60.0
            if accumulated_minutes + minutes > request.duration_minutes:
                break
            window.append(interval)
            accumulated_minutes += minutes
            previous_end = end
            if accumulated_minutes == request.duration_minutes:
                prices = [float(item["price_czk_kwh"]) for item in window]
                weighted_sum = sum(
                    float(item["price_czk_kwh"])
                    * ((item["_end"] - item["_start"]).total_seconds() / 3600.0)
                    for item in window
                )
                total_hours = request.duration_minutes / 60.0
                average = weighted_sum / total_hours
                energy = request.power_kw * total_hours if request.power_kw is not None else None
                cost = energy * average if energy is not None else None
                candidate = PlannedLoadWindow(
                    load_id=request.load_id,
                    starts_at=window[0]["_start"],
                    ends_at=window[-1]["_end"],
                    duration_minutes=request.duration_minutes,
                    average_czk_kwh=average,
                    minimum_czk_kwh=min(prices),
                    maximum_czk_kwh=max(prices),
                    estimated_energy_kwh=energy,
                    estimated_cost_czk=cost,
                    interval_count=len(window),
                )
                if best is None or candidate.average_czk_kwh < best.average_czk_kwh:
                    best = candidate
                break

    return best
