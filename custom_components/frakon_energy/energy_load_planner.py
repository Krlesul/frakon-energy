"""Device-independent load planning for FRAKON Energy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class FlexibleLoad:
    """A controllable load that needs a continuous run before its deadline."""

    load_id: str
    name: str
    duration_minutes: int
    power_kw: float
    earliest_start: datetime | None = None
    deadline: datetime | None = None

    def validated(self) -> "FlexibleLoad":
        if not self.load_id.strip():
            raise ValueError("load_id is required")
        if not self.name.strip():
            raise ValueError("load name is required")
        if self.duration_minutes <= 0 or self.duration_minutes % 15 != 0:
            raise ValueError("duration_minutes must be a positive multiple of 15")
        if self.power_kw <= 0:
            raise ValueError("power_kw must be positive")
        if self.earliest_start is not None and self.deadline is not None and self.earliest_start >= self.deadline:
            raise ValueError("earliest_start must be before deadline")
        return self


@dataclass(frozen=True, slots=True)
class LoadPlan:
    load_id: str
    name: str
    starts_at: str
    ends_at: str
    duration_minutes: int
    interval_count: int
    power_kw: float
    average_czk_kwh: float
    minimum_czk_kwh: float
    maximum_czk_kwh: float
    estimated_energy_kwh: float
    estimated_cost_czk: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_flexible_load(intervals: list[dict[str, Any]], load: FlexibleLoad) -> LoadPlan | None:
    """Choose the cheapest contiguous 15-minute run satisfying load constraints."""
    load.validated()

    count = load.duration_minutes // 15
    if len(intervals) < count:
        return None

    best: tuple[float, list[dict[str, Any]], list[float]] | None = None
    for start in range(len(intervals) - count + 1):
        window = intervals[start : start + count]
        try:
            starts_at = datetime.fromisoformat(str(window[0]["starts_at"]))
            ends_at = datetime.fromisoformat(str(window[-1]["ends_at"]))
            prices = [float(item["price_czk_kwh"]) for item in window]
        except (KeyError, TypeError, ValueError):
            continue

        if load.earliest_start is not None and starts_at < load.earliest_start:
            continue
        if load.deadline is not None and ends_at > load.deadline:
            continue

        average = sum(prices) / len(prices)
        if best is None or average < best[0]:
            best = (average, window, prices)

    if best is None:
        return None

    average, window, prices = best
    energy = load.power_kw * load.duration_minutes / 60
    return LoadPlan(
        load_id=load.load_id,
        name=load.name,
        starts_at=str(window[0]["starts_at"]),
        ends_at=str(window[-1]["ends_at"]),
        duration_minutes=load.duration_minutes,
        interval_count=count,
        power_kw=load.power_kw,
        average_czk_kwh=average,
        minimum_czk_kwh=min(prices),
        maximum_czk_kwh=max(prices),
        estimated_energy_kwh=energy,
        estimated_cost_czk=energy * average,
    )
