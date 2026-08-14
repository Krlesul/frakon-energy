"""Weekly cadence gate for confirmed active tariff source checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping

from .tariff_update_orchestrator import (
    PreparedActiveTariffSourceWatch,
    prepare_active_tariff_source_watch,
)

DEFAULT_TARIFF_UPDATE_INTERVAL = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class ActiveTariffCheckCadence:
    """Cadence decision for the one source watch authorized by confirmed state."""

    prepared: PreparedActiveTariffSourceWatch
    checked_at: datetime
    interval: timedelta
    due: bool
    last_checked_at: datetime | None
    next_due_at: datetime | None

    def __post_init__(self) -> None:
        if not isinstance(self.prepared, PreparedActiveTariffSourceWatch):
            raise ValueError("prepared must be PreparedActiveTariffSourceWatch")
        if not isinstance(self.checked_at, datetime) or self.checked_at.tzinfo is None:
            raise ValueError("checked_at must be a timezone-aware datetime")
        if not isinstance(self.interval, timedelta) or self.interval <= timedelta(0):
            raise ValueError("interval must be a positive timedelta")
        if not isinstance(self.due, bool):
            raise ValueError("due must be boolean")
        if self.last_checked_at is not None:
            if (
                not isinstance(self.last_checked_at, datetime)
                or self.last_checked_at.tzinfo is None
            ):
                raise ValueError("last_checked_at must be timezone-aware")
            expected_next = self.last_checked_at + self.interval
            if self.next_due_at != expected_next:
                raise ValueError("next_due_at does not match cadence interval")
            if self.due != (self.checked_at >= expected_next):
                raise ValueError("due flag does not match cadence timestamps")
        elif self.next_due_at is not None:
            raise ValueError("next_due_at requires last_checked_at")
        elif self.due is not True:
            raise ValueError("a never-checked active source must be due")


def active_tariff_check_cadence(
    options: Mapping[str, Any],
    *,
    day: date,
    checked_at: datetime,
    interval: timedelta = DEFAULT_TARIFF_UPDATE_INTERVAL,
) -> ActiveTariffCheckCadence:
    """Return whether the currently authorized source is due for another check.

    The authoritative watch is always reconciled from the current confirmed
    contract plus confirmed all-in tariff. Historical watches therefore cannot
    make an unrelated or superseded source due. A source without any prior check
    is immediately due; otherwise it is due only after the full interval.
    """
    if not isinstance(day, date):
        raise ValueError("day must be a date")
    if not isinstance(checked_at, datetime) or checked_at.tzinfo is None:
        raise ValueError("checked_at must be a timezone-aware datetime")
    if not isinstance(interval, timedelta) or interval <= timedelta(0):
        raise ValueError("interval must be a positive timedelta")

    prepared = prepare_active_tariff_source_watch(options, day=day)
    last_check = prepared.record.last_check
    if last_check is None:
        return ActiveTariffCheckCadence(
            prepared=prepared,
            checked_at=checked_at,
            interval=interval,
            due=True,
            last_checked_at=None,
            next_due_at=None,
        )

    last_checked_at = last_check.checked_at
    next_due_at = last_checked_at + interval
    return ActiveTariffCheckCadence(
        prepared=prepared,
        checked_at=checked_at,
        interval=interval,
        due=checked_at >= next_due_at,
        last_checked_at=last_checked_at,
        next_due_at=next_due_at,
    )
