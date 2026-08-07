"""Compatibility adapter for FRAKON Energy flexible-load planning."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from .energy_load_planner import FlexibleLoad, plan_flexible_load as plan_energy_load


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
    """Plan through the canonical energy-load engine while preserving the legacy payload."""
    request.validated()
    plan = plan_energy_load(
        intervals,
        FlexibleLoad(
            load_id=request.name.strip(),
            name=request.name,
            duration_minutes=request.duration_minutes,
            power_kw=request.power_kw,
            earliest_start=request.earliest_start,
            deadline=request.latest_end,
        ),
    )
    if plan is None:
        return None

    return {
        "load": asdict(request),
        "starts_at": plan.starts_at,
        "ends_at": plan.ends_at,
        "interval_count": plan.interval_count,
        "energy_kwh": plan.estimated_energy_kwh,
        "average_czk_kwh": plan.average_czk_kwh,
        "estimated_energy_cost_czk": plan.estimated_cost_czk,
        "minimum_czk_kwh": plan.minimum_czk_kwh,
        "maximum_czk_kwh": plan.maximum_czk_kwh,
    }
