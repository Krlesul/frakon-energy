from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time


@dataclass(frozen=True, slots=True)
class TimelineInterval:
    start: time
    end: time
    low_tariff: bool = True

    def start_minutes(self) -> int:
        return self.start.hour * 60 + self.start.minute

    def end_minutes(self) -> int:
        return self.end.hour * 60 + self.end.minute


@dataclass(frozen=True, slots=True)
class TimelineMarker:
    label: str
    position_percent: float
    major: bool


@dataclass(frozen=True, slots=True)
class TimelineSnapshot:
    current_time_label: str
    current_position_percent: float
    current_low_tariff: bool
    intervals: tuple[dict[str, object], ...]
    desktop_markers: tuple[TimelineMarker, ...]
    compact_markers: tuple[TimelineMarker, ...]


def _position(minutes: int) -> float:
    return round(max(0, min(1440, minutes)) / 1440 * 100, 4)


def _contains(interval: TimelineInterval, minute: int) -> bool:
    start = interval.start_minutes()
    end = interval.end_minutes()
    if end >= start:
        return start <= minute < end
    return minute >= start or minute < end


def build_timeline_snapshot(
    *,
    now: datetime,
    intervals: tuple[TimelineInterval, ...],
) -> TimelineSnapshot:
    minute = now.hour * 60 + now.minute
    encoded = tuple(
        {
            "start": interval.start.strftime("%H:%M"),
            "end": interval.end.strftime("%H:%M"),
            "start_percent": _position(interval.start_minutes()),
            "end_percent": _position(interval.end_minutes()),
            "low_tariff": interval.low_tariff,
        }
        for interval in intervals
    )
    desktop = tuple(
        TimelineMarker(f"{hour:02d}", _position(hour * 60), hour % 4 == 0)
        for hour in range(25)
    )
    compact = tuple(
        TimelineMarker(f"{hour:02d}", _position(hour * 60), hour % 4 == 0)
        for hour in range(0, 25, 2)
    )
    return TimelineSnapshot(
        current_time_label=now.strftime("%H:%M"),
        current_position_percent=_position(minute),
        current_low_tariff=any(
            interval.low_tariff and _contains(interval, minute) for interval in intervals
        ),
        intervals=encoded,
        desktop_markers=desktop,
        compact_markers=compact,
    )
