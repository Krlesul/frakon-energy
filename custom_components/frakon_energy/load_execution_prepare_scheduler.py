"""Internal prepare-only scheduler for durable FRAKON Energy execution schedules.

The scheduler may create only an inert durable ``prepared`` lifecycle through the
existing guarded ``prepare_scheduled`` bridge. It never dispatches an action and
never calls a Home Assistant service.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time

from .const import DOMAIN
from .load_execution_lifecycle_recovery import RECOVERY_OK, lifecycle_recovery_summary
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_execution_prepare_scheduled_ws_api import async_prepare_scheduled_execution
from .load_execution_schedule_diagnostics import (
    TIMING_EXPIRED,
    TIMING_LIFECYCLE_EXISTS,
    TIMING_MISSED,
    TIMING_PREPARE_NOW,
    TIMING_WAITING,
    evaluate_schedule_timing,
)
from .load_execution_schedule_runtime import schedule_repository

_RUNTIME_KEY = "load_execution_prepare_schedulers_by_entry"

STATUS_SCHEDULED = "scheduled"
STATUS_PREPARING = "preparing"
STATUS_PREPARED = "prepared"
STATUS_REJECTED = "rejected"
STATUS_MISSED = "missed"
STATUS_EXPIRED = "expired"
STATUS_BLOCKED_RECOVERY = "blocked_recovery"
STATUS_LIFECYCLE_EXISTS = "lifecycle_exists"


@dataclass(frozen=True, slots=True)
class PrepareSchedulerStatus:
    attempt_id: str
    schedule_id: str
    status: str
    next_wake_at: str | None = None
    last_attempt_at: str | None = None
    last_error: str | None = None
    lifecycle_state: str | None = None
    timer_active: bool = False
    prepare_only: bool = True
    execution_performed: bool = False
    service_call_performed: bool = False
    executor_available: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExecutionPrepareScheduler:
    """Runtime timer layer derived entirely from durable execution records."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        if not entry_id:
            raise ValueError("entry_id is required")
        self._hass = hass
        self._entry_id = entry_id
        self._lock = asyncio.Lock()
        self._started = False
        self._unsub_by_attempt: dict[str, Callable[[], None]] = {}
        self._status_by_attempt: dict[str, PrepareSchedulerStatus] = {}

    @property
    def entry_id(self) -> str:
        return self._entry_id

    @property
    def started(self) -> bool:
        return self._started

    def statuses(self) -> tuple[PrepareSchedulerStatus, ...]:
        return tuple(sorted(self._status_by_attempt.values(), key=lambda item: item.attempt_id))

    async def async_start(self) -> None:
        if self._started:
            await self.async_refresh()
            return
        self._started = True
        await self.async_refresh()

    async def async_stop(self) -> None:
        self._started = False
        async with self._lock:
            self._cancel_all_timers()

    def _cancel_all_timers(self) -> None:
        callbacks = tuple(self._unsub_by_attempt.values())
        self._unsub_by_attempt.clear()
        for unsubscribe in callbacks:
            unsubscribe()

    @callback
    def _timer_fired(self, attempt_id: str, fired_at: datetime) -> None:
        """Convert one HA time callback into an async prepare task."""
        self._unsub_by_attempt.pop(attempt_id, None)
        if not self._started:
            return
        self._hass.async_create_task(self._async_prepare_attempt(attempt_id, fired_at))

    def _schedule_timer(self, attempt_id: str, starts_at: datetime) -> None:
        @callback
        def timer_action(now: datetime) -> None:
            self._timer_fired(attempt_id, now)

        unsubscribe = async_track_point_in_utc_time(
            self._hass,
            timer_action,
            starts_at.astimezone(timezone.utc),
        )
        self._unsub_by_attempt[attempt_id] = unsubscribe

    async def _async_prepare_attempt(self, attempt_id: str, now: datetime) -> None:
        schedule = await schedule_repository(self._hass, self._entry_id).async_get_by_attempt_id(attempt_id)
        if schedule is None:
            self._status_by_attempt.pop(attempt_id, None)
            return
        timestamp = now.astimezone(timezone.utc).isoformat()
        self._status_by_attempt[attempt_id] = PrepareSchedulerStatus(
            attempt_id=attempt_id,
            schedule_id=schedule.schedule_id,
            status=STATUS_PREPARING,
            last_attempt_at=timestamp,
            timer_active=False,
        )
        try:
            result = await async_prepare_scheduled_execution(
                self._hass,
                entry_id=self._entry_id,
                attempt_id=attempt_id,
                now=now,
            )
        except Exception as err:
            self._status_by_attempt[attempt_id] = PrepareSchedulerStatus(
                attempt_id=attempt_id,
                schedule_id=schedule.schedule_id,
                status=STATUS_REJECTED,
                last_attempt_at=timestamp,
                last_error=str(err),
                timer_active=False,
            )
            return

        lifecycle_payload = result.get("lifecycle")
        lifecycle = lifecycle_payload.get("lifecycle") if isinstance(lifecycle_payload, dict) else None
        lifecycle_state = lifecycle.get("state") if isinstance(lifecycle, dict) else None
        self._status_by_attempt[attempt_id] = PrepareSchedulerStatus(
            attempt_id=attempt_id,
            schedule_id=schedule.schedule_id,
            status=STATUS_PREPARED if lifecycle_state == "prepared" else STATUS_LIFECYCLE_EXISTS,
            last_attempt_at=timestamp,
            lifecycle_state=str(lifecycle_state) if lifecycle_state is not None else None,
            timer_active=False,
            execution_performed=result.get("execution_performed") is True,
            service_call_performed=result.get("service_call_performed") is True,
        )

    async def async_refresh(self, *, now: datetime | None = None) -> None:
        """Rebuild timers from durable schedules/lifecycles and prepare immediate candidates."""
        if not self._started:
            return
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        immediate: list[str] = []
        async with self._lock:
            self._cancel_all_timers()
            schedules = await schedule_repository(self._hass, self._entry_id).async_list()
            lifecycles = await lifecycle_repository(self._hass, self._entry_id).async_list()
            lifecycle_by_attempt = {item.attempt_id: item for item in lifecycles}
            recovery_ready = lifecycle_recovery_summary(self._hass, self._entry_id).status == RECOVERY_OK
            new_status: dict[str, PrepareSchedulerStatus] = {}

            for schedule in schedules:
                lifecycle = lifecycle_by_attempt.get(schedule.attempt_id)
                diagnostic = evaluate_schedule_timing(
                    schedule,
                    lifecycle=lifecycle,
                    now=current,
                    recovery_ready=recovery_ready,
                )
                if diagnostic.status == TIMING_LIFECYCLE_EXISTS:
                    new_status[schedule.attempt_id] = PrepareSchedulerStatus(
                        attempt_id=schedule.attempt_id,
                        schedule_id=schedule.schedule_id,
                        status=STATUS_LIFECYCLE_EXISTS,
                        lifecycle_state=diagnostic.lifecycle_state,
                    )
                    continue
                if not recovery_ready:
                    new_status[schedule.attempt_id] = PrepareSchedulerStatus(
                        attempt_id=schedule.attempt_id,
                        schedule_id=schedule.schedule_id,
                        status=STATUS_BLOCKED_RECOVERY,
                    )
                    continue
                if diagnostic.status == TIMING_WAITING:
                    starts = datetime.fromisoformat(schedule.plan.starts_at)
                    self._schedule_timer(schedule.attempt_id, starts)
                    new_status[schedule.attempt_id] = PrepareSchedulerStatus(
                        attempt_id=schedule.attempt_id,
                        schedule_id=schedule.schedule_id,
                        status=STATUS_SCHEDULED,
                        next_wake_at=starts.astimezone(timezone.utc).isoformat(),
                        timer_active=True,
                    )
                    continue
                if diagnostic.status == TIMING_PREPARE_NOW:
                    immediate.append(schedule.attempt_id)
                    new_status[schedule.attempt_id] = PrepareSchedulerStatus(
                        attempt_id=schedule.attempt_id,
                        schedule_id=schedule.schedule_id,
                        status=STATUS_PREPARING,
                        timer_active=False,
                    )
                    continue
                if diagnostic.status == TIMING_MISSED:
                    new_status[schedule.attempt_id] = PrepareSchedulerStatus(
                        attempt_id=schedule.attempt_id,
                        schedule_id=schedule.schedule_id,
                        status=STATUS_MISSED,
                    )
                    continue
                if diagnostic.status == TIMING_EXPIRED:
                    new_status[schedule.attempt_id] = PrepareSchedulerStatus(
                        attempt_id=schedule.attempt_id,
                        schedule_id=schedule.schedule_id,
                        status=STATUS_EXPIRED,
                    )
                    continue
            self._status_by_attempt = new_status

        for attempt_id in immediate:
            await self._async_prepare_attempt(attempt_id, current)


def _scheduler_map(hass: HomeAssistant) -> dict[str, ExecutionPrepareScheduler]:
    domain_data = hass.data.setdefault(DOMAIN, {})
    value = domain_data.get(_RUNTIME_KEY)
    if not isinstance(value, dict):
        value = {}
        domain_data[_RUNTIME_KEY] = value
    return value


def prepare_scheduler(hass: HomeAssistant, entry_id: str) -> ExecutionPrepareScheduler:
    schedulers = _scheduler_map(hass)
    scheduler = schedulers.get(entry_id)
    if isinstance(scheduler, ExecutionPrepareScheduler):
        return scheduler
    scheduler = ExecutionPrepareScheduler(hass, entry_id)
    schedulers[entry_id] = scheduler
    return scheduler


async def async_start_prepare_scheduler(hass: HomeAssistant, entry_id: str) -> ExecutionPrepareScheduler:
    scheduler = prepare_scheduler(hass, entry_id)
    await scheduler.async_start()
    return scheduler


async def async_refresh_prepare_scheduler_if_started(hass: HomeAssistant, entry_id: str) -> None:
    scheduler = _scheduler_map(hass).get(entry_id)
    if isinstance(scheduler, ExecutionPrepareScheduler) and scheduler.started:
        await scheduler.async_refresh()


async def async_stop_prepare_scheduler(hass: HomeAssistant, entry_id: str) -> None:
    scheduler = _scheduler_map(hass).pop(entry_id, None)
    if isinstance(scheduler, ExecutionPrepareScheduler):
        await scheduler.async_stop()
