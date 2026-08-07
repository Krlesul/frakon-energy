"""Internal timer scheduler for durable FRAKON Energy stop obligations.

The scheduler wakes at persisted stop deadlines and may only perform audit-safe
no-op/verification resolution. It never calls a Home Assistant service and never
starts or retries a physical stop dispatch.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time

from .const import DOMAIN
from .load_execution_stop_due_gate import (
    STOP_DUE_ALREADY_OFF,
    STOP_DUE_BLOCKED,
    STOP_DUE_COMPLETED,
    STOP_DUE_READY,
    STOP_DUE_RECOVERY_REVIEW,
    STOP_DUE_SAFE_TO_VERIFY,
    STOP_DUE_WAITING,
    evaluate_stop_due_gate,
)
from .load_execution_stop_lifecycle import (
    STOP_STATE_FAILED,
    ExecutionStopLifecycleRecord,
)
from .load_execution_stop_lifecycle_runtime import stop_lifecycle_repository
from .load_execution_stop_recovery import STOP_RECOVERY_OK, stop_recovery_summary
from .load_execution_stop_resolution import (
    async_complete_stop_noop,
    async_verify_stop_resolution,
)

_RUNTIME_KEY = "load_execution_stop_schedulers_by_entry"

STATUS_SCHEDULED = "scheduled"
STATUS_PROCESSING = "processing"
STATUS_READY_TO_STOP = "ready_to_stop"
STATUS_SATISFIED = "satisfied"
STATUS_VERIFIED = "verified"
STATUS_COMPLETED = "completed"
STATUS_RECOVERY_REVIEW = "recovery_review"
STATUS_BLOCKED = "blocked"
STATUS_FAILED = "failed"
STATUS_ERROR = "error"


@dataclass(frozen=True, slots=True)
class StopSchedulerStatus:
    start_lifecycle_id: str
    stop_lifecycle_id: str
    entity_id: str
    status: str
    ends_at: str
    next_wake_at: str | None = None
    last_processed_at: str | None = None
    last_error: str | None = None
    timer_active: bool = False
    dispatch_required: bool = False
    resolution_performed: bool = False
    service_call_performed: bool | None = False
    execution_performed: bool = False
    executor_available: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExecutionStopScheduler:
    """Runtime timer layer derived only from durable stop lifecycle records."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        if not entry_id:
            raise ValueError("entry_id is required")
        self._hass = hass
        self._entry_id = entry_id
        self._lock = asyncio.Lock()
        self._started = False
        self._healthy = True
        self._last_error: str | None = None
        self._unsub_by_start_lifecycle: dict[str, Callable[[], None]] = {}
        self._status_by_start_lifecycle: dict[str, StopSchedulerStatus] = {}

    @property
    def entry_id(self) -> str:
        return self._entry_id

    @property
    def started(self) -> bool:
        return self._started

    @property
    def healthy(self) -> bool:
        return self._healthy

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def statuses(self) -> tuple[StopSchedulerStatus, ...]:
        return tuple(
            sorted(
                self._status_by_start_lifecycle.values(),
                key=lambda item: item.start_lifecycle_id,
            )
        )

    async def async_start(self) -> None:
        if self._started:
            await self.async_refresh()
            return
        self._started = True
        try:
            await self.async_refresh()
        except Exception as err:
            async with self._lock:
                self._cancel_all_timers()
                self._healthy = False
                self._last_error = str(err)

    async def async_stop(self) -> None:
        self._started = False
        async with self._lock:
            self._cancel_all_timers()

    def _cancel_all_timers(self) -> None:
        callbacks = tuple(self._unsub_by_start_lifecycle.values())
        self._unsub_by_start_lifecycle.clear()
        for unsubscribe in callbacks:
            unsubscribe()

    @callback
    def _timer_fired(self, start_lifecycle_id: str, fired_at: datetime) -> None:
        self._unsub_by_start_lifecycle.pop(start_lifecycle_id, None)
        if not self._started:
            return
        self._hass.async_create_task(
            self._async_process(start_lifecycle_id, fired_at)
        )

    def _schedule_timer(self, record: ExecutionStopLifecycleRecord, ends_at: datetime) -> None:
        @callback
        def timer_action(now: datetime) -> None:
            self._timer_fired(record.start_lifecycle_id, now)

        unsubscribe = async_track_point_in_utc_time(
            self._hass,
            timer_action,
            ends_at.astimezone(timezone.utc),
        )
        self._unsub_by_start_lifecycle[record.start_lifecycle_id] = unsubscribe

    def _live_state(self, record: ExecutionStopLifecycleRecord) -> str | None:
        state = self._hass.states.get(record.entity_id)
        return str(state.state) if state is not None else None

    @staticmethod
    def _timestamp(now: datetime) -> str:
        return now.astimezone(timezone.utc).isoformat()

    async def _async_process(self, start_lifecycle_id: str, now: datetime) -> None:
        if not self._started:
            return
        repository = stop_lifecycle_repository(self._hass, self._entry_id)
        record = await repository.async_get_by_start_lifecycle_id(start_lifecycle_id)
        if record is None:
            self._status_by_start_lifecycle.pop(start_lifecycle_id, None)
            return
        recovery_ready = (
            stop_recovery_summary(self._hass, self._entry_id).status == STOP_RECOVERY_OK
        )
        decision = evaluate_stop_due_gate(
            record=record,
            current_state=self._live_state(record),
            now=now,
            recovery_ready=recovery_ready,
        )
        timestamp = self._timestamp(now)
        self._status_by_start_lifecycle[start_lifecycle_id] = StopSchedulerStatus(
            start_lifecycle_id=start_lifecycle_id,
            stop_lifecycle_id=record.stop_lifecycle_id,
            entity_id=record.entity_id,
            status=STATUS_PROCESSING,
            ends_at=record.ends_at,
            last_processed_at=timestamp,
        )

        if not self._started:
            return
        try:
            if decision.status == STOP_DUE_ALREADY_OFF and decision.can_complete_noop:
                result = await async_complete_stop_noop(
                    self._hass,
                    entry_id=self._entry_id,
                    start_lifecycle_id=start_lifecycle_id,
                    now=now,
                )
                self._status_by_start_lifecycle[start_lifecycle_id] = StopSchedulerStatus(
                    start_lifecycle_id=start_lifecycle_id,
                    stop_lifecycle_id=record.stop_lifecycle_id,
                    entity_id=record.entity_id,
                    status=STATUS_SATISFIED,
                    ends_at=record.ends_at,
                    last_processed_at=timestamp,
                    resolution_performed=result.get("resolution_performed") is True,
                    service_call_performed=False,
                )
                return
            if decision.status == STOP_DUE_SAFE_TO_VERIFY and decision.can_mark_verified:
                result = await async_verify_stop_resolution(
                    self._hass,
                    entry_id=self._entry_id,
                    start_lifecycle_id=start_lifecycle_id,
                    now=now,
                )
                self._status_by_start_lifecycle[start_lifecycle_id] = StopSchedulerStatus(
                    start_lifecycle_id=start_lifecycle_id,
                    stop_lifecycle_id=record.stop_lifecycle_id,
                    entity_id=record.entity_id,
                    status=STATUS_VERIFIED,
                    ends_at=record.ends_at,
                    last_processed_at=timestamp,
                    resolution_performed=result.get("resolution_performed") is True,
                    service_call_performed=result.get("service_call_performed"),
                )
                return
        except Exception as err:
            self._status_by_start_lifecycle[start_lifecycle_id] = StopSchedulerStatus(
                start_lifecycle_id=start_lifecycle_id,
                stop_lifecycle_id=record.stop_lifecycle_id,
                entity_id=record.entity_id,
                status=STATUS_ERROR,
                ends_at=record.ends_at,
                last_processed_at=timestamp,
                last_error=str(err),
            )
            return

        if decision.status == STOP_DUE_READY and decision.can_dispatch_stop:
            status = STATUS_READY_TO_STOP
            dispatch_required = True
        elif decision.status == STOP_DUE_RECOVERY_REVIEW:
            status = STATUS_RECOVERY_REVIEW
            dispatch_required = False
        elif decision.status == STOP_DUE_COMPLETED:
            status = STATUS_COMPLETED
            dispatch_required = False
        elif decision.status == STOP_DUE_BLOCKED:
            status = STATUS_FAILED if record.state == STOP_STATE_FAILED else STATUS_BLOCKED
            dispatch_required = False
        elif decision.status == STOP_DUE_WAITING:
            status = STATUS_SCHEDULED
            dispatch_required = False
        else:
            status = STATUS_BLOCKED
            dispatch_required = False

        self._status_by_start_lifecycle[start_lifecycle_id] = StopSchedulerStatus(
            start_lifecycle_id=start_lifecycle_id,
            stop_lifecycle_id=record.stop_lifecycle_id,
            entity_id=record.entity_id,
            status=status,
            ends_at=record.ends_at,
            last_processed_at=timestamp,
            dispatch_required=dispatch_required,
        )

    async def async_refresh(self, *, now: datetime | None = None) -> None:
        """Rebuild timers from durable stop lifecycles and process due safe work."""
        if not self._started:
            return
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        immediate: list[str] = []
        async with self._lock:
            self._cancel_all_timers()
            records = await stop_lifecycle_repository(
                self._hass,
                self._entry_id,
            ).async_list()
            recovery_ready = (
                stop_recovery_summary(self._hass, self._entry_id).status == STOP_RECOVERY_OK
            )
            new_status: dict[str, StopSchedulerStatus] = {}

            for record in records:
                decision = evaluate_stop_due_gate(
                    record=record,
                    current_state=self._live_state(record),
                    now=current,
                    recovery_ready=recovery_ready,
                )
                if decision.status == STOP_DUE_WAITING:
                    ends = datetime.fromisoformat(record.ends_at)
                    self._schedule_timer(record, ends)
                    new_status[record.start_lifecycle_id] = StopSchedulerStatus(
                        start_lifecycle_id=record.start_lifecycle_id,
                        stop_lifecycle_id=record.stop_lifecycle_id,
                        entity_id=record.entity_id,
                        status=STATUS_SCHEDULED,
                        ends_at=record.ends_at,
                        next_wake_at=ends.astimezone(timezone.utc).isoformat(),
                        timer_active=True,
                    )
                    continue
                if decision.status in (STOP_DUE_ALREADY_OFF, STOP_DUE_SAFE_TO_VERIFY):
                    immediate.append(record.start_lifecycle_id)
                    new_status[record.start_lifecycle_id] = StopSchedulerStatus(
                        start_lifecycle_id=record.start_lifecycle_id,
                        stop_lifecycle_id=record.stop_lifecycle_id,
                        entity_id=record.entity_id,
                        status=STATUS_PROCESSING,
                        ends_at=record.ends_at,
                    )
                    continue
                if decision.status == STOP_DUE_READY:
                    status = STATUS_READY_TO_STOP
                    dispatch_required = True
                elif decision.status == STOP_DUE_RECOVERY_REVIEW:
                    status = STATUS_RECOVERY_REVIEW
                    dispatch_required = False
                elif decision.status == STOP_DUE_COMPLETED:
                    status = STATUS_COMPLETED
                    dispatch_required = False
                elif decision.status == STOP_DUE_BLOCKED:
                    status = STATUS_FAILED if record.state == STOP_STATE_FAILED else STATUS_BLOCKED
                    dispatch_required = False
                else:
                    status = STATUS_BLOCKED
                    dispatch_required = False
                new_status[record.start_lifecycle_id] = StopSchedulerStatus(
                    start_lifecycle_id=record.start_lifecycle_id,
                    stop_lifecycle_id=record.stop_lifecycle_id,
                    entity_id=record.entity_id,
                    status=status,
                    ends_at=record.ends_at,
                    dispatch_required=dispatch_required,
                )
            self._status_by_start_lifecycle = new_status
            self._healthy = True
            self._last_error = None

        for start_lifecycle_id in immediate:
            if not self._started:
                return
            await self._async_process(start_lifecycle_id, current)


def _scheduler_map(hass: HomeAssistant) -> dict[str, ExecutionStopScheduler]:
    domain_data = hass.data.setdefault(DOMAIN, {})
    value = domain_data.get(_RUNTIME_KEY)
    if not isinstance(value, dict):
        value = {}
        domain_data[_RUNTIME_KEY] = value
    return value


def stop_scheduler(hass: HomeAssistant, entry_id: str) -> ExecutionStopScheduler:
    schedulers = _scheduler_map(hass)
    scheduler = schedulers.get(entry_id)
    if isinstance(scheduler, ExecutionStopScheduler):
        return scheduler
    scheduler = ExecutionStopScheduler(hass, entry_id)
    schedulers[entry_id] = scheduler
    return scheduler


async def async_start_stop_scheduler(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionStopScheduler:
    scheduler = stop_scheduler(hass, entry_id)
    await scheduler.async_start()
    return scheduler


async def async_refresh_stop_scheduler_if_started(
    hass: HomeAssistant,
    entry_id: str,
) -> None:
    scheduler = _scheduler_map(hass).get(entry_id)
    if isinstance(scheduler, ExecutionStopScheduler) and scheduler.started:
        try:
            await scheduler.async_refresh()
        except Exception as err:
            scheduler._healthy = False
            scheduler._last_error = str(err)


async def async_stop_stop_scheduler(hass: HomeAssistant, entry_id: str) -> None:
    scheduler = _scheduler_map(hass).pop(entry_id, None)
    if isinstance(scheduler, ExecutionStopScheduler):
        await scheduler.async_stop()
