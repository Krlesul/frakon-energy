"""Compatibility adapter for legacy FRAKON Energy spot-load planning calls.

The canonical planning algorithm lives in ``load_planner.py``. This module keeps
older callers working without maintaining a second scheduling implementation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from .load_planner import FlexibleLoadRequest as CoreLoadRequest
from .load_planner import plan_contiguous_load


@dataclass(frozen=True, slots=True)
class FlexibleLoadRequest:
    """Legacy request shape retained for compatibility."""

    name: str
    duration_minutes: int
    power_kw: float
    earliest_start: datetime | None = None
    latest_end: datetime | None = None

    def validated(self) -> "FlexibleLoadRequest":
        if not self.name.strip():
            raise ValueError("load name is required")
        if self.duration_minutes <= 0:
            raise ValueError("duration must be positive")
        if self.power_kw <= 0:
            raise ValueError("power must be greater than zero")
        if self.earliest_start and self.latest_end and self.earliest_start >= self.latest_end:
            raise ValueError("earliest start must be before latest end")
        return self


def plan_flexible_load(
    intervals: list[dict[str, Any]], request: FlexibleLoadRequest
) -> dict[str, Any] | None:
    """Plan through the canonical engine while preserving the legacy payload."""
    request.validated()
    plan = plan_contiguous_load(
        intervals,
        CoreLoadRequest(
            load_id=request.name.strip(),
            duration_minutes=request.duration_minutes,
            earliest_start=request.earliest_start,
            latest_end=request.latest_end,
            power_kw=request.power_kw,
        ),
    )
    if plan is None:
        return None

    return {
        "load": asdict(request),
        "starts_at": plan.starts_at.isoformat(),
        "ends_at": plan.ends_at.isoformat(),
        "interval_count": plan.interval_count,
        "energy_kwh": plan.estimated_energy_kwh,
        "average_czk_kwh": plan.average_czk_kwh,
        "estimated_energy_cost_czk": plan.estimated_cost_czk,
        "minimum_czk_kwh": plan.minimum_czk_kwh,
        "maximum_czk_kwh": plan.maximum_czk_kwh,
    }
