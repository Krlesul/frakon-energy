"""Restart-safe scheduler for already-consumed FRAKON Energy pending runs.

The scheduler owns no approval and contains no Home Assistant service call. At
an exact persisted plan start it may only invoke the existing lifecycle prepare
and durable stop-lease prepare flows. Any physical start can happen only later
through the already isolated ARM-gated bounded start scheduler/dispatcher.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time

from .const import DOMAIN
from .load_execution_lifecycle import (
    STATE_CANCELLED,
    STATE_DISPATCHED,
    STATE_DISPATCHING,
    STATE_FAILED,
    STATE_PREPARED,
    STATE_RECOVERY_REQUIRED,
    STATE_VERIFIED,
)
from .load_execution_lifecycle_recovery import RECOVERY_OK, lifecycle_recovery_summary
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_execution_lifecycle_ws_api import async_prepare_execution_lifecycle
from .load_execution_pending_run import ExecutionPendingRun
from .load_execution_pending_run_runtime import pending_run_repository
from .load_execution_readiness import (
    DEFAULT_START_GRACE_SECONDS,
    READINESS_ALREADY_SATISFIED,
)
from .load_execution_readiness_ws_api import async_execution_readiness
from .load_execution_start_scheduler import start_scheduler
from .load_execution_stop_recovery import STOP_RECOVERY_OK, stop_recovery_summary
from .load_execution_stop_scheduler import stop_scheduler
from .load_execution_stop_lease_ws_api import async_prepare_stop_lease

_RUNTIME_KEY = "load_execution_pending_run_schedulers_by_entry"

STOP_LEASE_RETRY_SECONDS = 5

STATUS_SCHEDULED = "scheduled"
STATUS_PREPARING = "preparing"
STATUS_RETRYING_STOP_LEASE = "retrying_stop_lease"
STATUS_NO_START_NEEDED = "no_start_needed"
STATUS_PREPARED_WITH_STOP_LEASE = "prepared_with_stop_lease"
STATUS_DELEGATED = "delegated_to_start_scheduler"
STATUS_EXISTING_LIFECYCLE = "existing_lifecycle"
STATUS_MISSED = "missed_start_window"
STATUS_BLOCKED = "blocked"
STATUS_ERROR = "error"

_TERMINAL_LIFECYCLE_STATES = {
    STATE_DISPATCHING,
    STATE_DISPATCHED,
    STATE_RECOVERY_REQUIRED,
    STATE_VERIFIED,
    STATE_FAILED,
    STATE_CANCELLED,
}


@dataclass(frozen=True, slots=True)
class PendingRunSchedulerStatus:
    pending_run_id: str
    attempt_id: str
    entity_id: str
    status: str
    starts_at: str
    ends_at: str
    next_wake_at: str | None = None
    last_processed_at: str | None = None
    last_error: str | None = None
    timer_active: bool = False
    lifecycle_prepared: bool = False
    stop_lease_prepared: bool = False
    retry_count: int = 0
    service_call_performed: bool = False
    execution_performed: bool = False
    executor_available: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExecutionPendingRunScheduler:
    """Timer runtime derived only from durable inert pending-run records."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        if not entry_id:
            raise ValueError("entry_id is required")
        self._hass = hass
        self._entry_id = entry_id
        self._lock = asyncio.Lock()
        self._started = False
        self._healthy = True
        self._last_error: str | None = None
        self._unsub_by_attempt: dict[str, Callable[[], None]] = {}
        self._status_by_attempt: dict[str, PendingRunSchedulerStatus] = {}
        self._retry_count_by_attempt: dict[str, int] = {}

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

    def statuses(self) -> tuple[PendingRunSchedulerStatus, ...]:
        return tuple(
            sorted(self._status_by_attempt.values(), key=lambda item: item.attempt_id)
        )

    @staticmethod
    def _timestamp(now: datetime) -> str:
        return now.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _starts_at(record: ExecutionPendingRun) -> datetime:
        return datetime.fromisoformat(record.plan.starts_at)

    @staticmethod
    def _grace_deadline(record: ExecutionPendingRun) -> datetime:
        return ExecutionPendingRunScheduler._starts_at(record) + timedelta(
            seconds=DEFAULT_START_GRACE_SECONDS
        )

    def _dependencies_ready(self) -> tuple[bool, str | None]:
        start_recovery = lifecycle_recovery_summary(self._hass, self._entry_id)
        stop_recovery = stop_recovery_summary(self._hass, self._entry_id)
        start_runtime = start_scheduler(self._hass, self._entry_id)
        stop_runtime = stop_scheduler(self._hass, self._entry_id)
        if start_recovery.status != RECOVERY_OK:
            return False, f"start_recovery:{start_recovery.status}"
        if stop_recovery.status != STOP_RECOVERY_OK:
            return False, f"stop_recovery:{stop_recovery.status}"
        if not stop_runtime.started or not stop_runtime.healthy:
            return False, "autonomous_stop_runtime_not_ready"
        if not start_runtime.started or not start_runtime.healthy:
            return False, "autonomous_start_runtime_not_ready"
        return True, None

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
        callbacks = tuple(self._unsub_by_attempt.values())
        self._unsub_by_attempt.clear()
        for unsubscribe in callbacks:
            unsubscribe()

    @callback
    def _timer_fired(self, attempt_id: str, fired_at: datetime) -> None:
        self._unsub_by_attempt.pop(attempt_id, None)
        if not self._started:
            return
        self._hass.async_create_task(self._async_process(attempt_id, fired_at))

    def _schedule_timer(self, record: ExecutionPendingRun, when: datetime) -> None:
        @callback
        def timer_action(now: datetime) -> None:
            self._timer_fired(record.attempt_id, now)

        previous = self._unsub_by_attempt.pop(record.attempt_id, None)
        if previous is not None:
            previous()
        unsubscribe = async_track_point_in_utc_time(
            self._hass,
            timer_action,
            when.astimezone(timezone.utc),
        )
        self._unsub_by_attempt[record.attempt_id] = unsubscribe

    def _set_status(
        self,
        record: ExecutionPendingRun,
        *,
        status: str,
        now: datetime | None = None,
        next_wake_at: str | None = None,
        timer_active: bool = False,
        last_error: str | None = None,
        lifecycle_prepared: bool = False,
        stop_lease_prepared: bool = False,
        retry_count: int | None = None,
    ) -> None:
        self._status_by_attempt[record.attempt_id] = PendingRunSchedulerStatus(
            pending_run_id=record.pending_run_id,
            attempt_id=record.attempt_id,
            entity_id=record.entity_id,
            status=status,
            starts_at=record.plan.starts_at,
            ends_at=record.plan.ends_at,
            next_wake_at=next_wake_at,
            last_processed_at=self._timestamp(now) if now is not None else None,
            last_error=last_error,
            timer_active=timer_active,
            lifecycle_prepared=lifecycle_prepared,
            stop_lease_prepared=stop_lease_prepared,
            retry_count=(
                self._retry_count_by_attempt.get(record.attempt_id, 0)
                if retry_count is None
                else retry_count
            ),
        )

    def _clear_retry(self, attempt_id: str) -> None:
        self._retry_count_by_attempt.pop(attempt_id, None)

    async def _already_satisfied_after_prepare_rejection(
        self,
        record: ExecutionPendingRun,
        *,
        now: datetime,
    ) -> bool:
        """Reclassify only the exact authoritative already-satisfied outcome."""
        try:
            payload = await async_execution_readiness(
                self._hass,
                entry_id=self._entry_id,
                attempt_id=record.attempt_id,
                plan_value=record.plan.as_dict(),
                now=now,
            )
        except Exception:
            return False
        readiness = payload.get("readiness")
        if not isinstance(readiness, dict):
            return False
        if readiness.get("status") != READINESS_ALREADY_SATISFIED:
            return False
        self._clear_retry(record.attempt_id)
        self._set_status(
            record,
            status=STATUS_NO_START_NEEDED,
            now=now,
            lifecycle_prepared=False,
            stop_lease_prepared=False,
            retry_count=0,
        )
        return True

    def _schedule_stop_lease_retry(
        self,
        record: ExecutionPendingRun,
        *,
        now: datetime,
        grace_deadline: datetime,
        error: Exception,
    ) -> bool:
        """Schedule one safe retry only while no physical start can yet occur."""
        if now >= grace_deadline:
            return False
        retry_at = min(
            now + timedelta(seconds=STOP_LEASE_RETRY_SECONDS),
            grace_deadline,
        )
        if retry_at <= now:
            return False
        count = self._retry_count_by_attempt.get(record.attempt_id, 0) + 1
        self._retry_count_by_attempt[record.attempt_id] = count
        self._schedule_timer(record, retry_at)
        self._set_status(
            record,
            status=STATUS_RETRYING_STOP_LEASE,
            now=now,
            next_wake_at=retry_at.astimezone(timezone.utc).isoformat(),
            timer_active=True,
            last_error=str(error),
            lifecycle_prepared=True,
            stop_lease_prepared=False,
            retry_count=count,
        )
        return True

    async def _async_process(self, attempt_id: str, now: datetime) -> None:
        if not self._started:
            return
        async with self._lock:
            if not self._started:
                return
            record = await pending_run_repository(
                self._hass, self._entry_id
            ).async_get_by_attempt_id(attempt_id)
            if record is None:
                self._status_by_attempt.pop(attempt_id, None)
                self._clear_retry(attempt_id)
                return
            dependencies_ready, dependency_error = self._dependencies_ready()
            if not dependencies_ready:
                self._healthy = False
                self._last_error = dependency_error
                self._set_status(
                    record,
                    status=STATUS_BLOCKED,
                    now=now,
                    last_error=dependency_error,
                )
                return
            self._healthy = True
            self._last_error = None

            starts_at = self._starts_at(record)
            grace_deadline = self._grace_deadline(record)
            if now < starts_at:
                self._set_status(
                    record,
                    status=STATUS_SCHEDULED,
                    now=now,
                    next_wake_at=starts_at.astimezone(timezone.utc).isoformat(),
                )
                return
            if now > grace_deadline:
                lifecycle = await lifecycle_repository(
                    self._hass, self._entry_id
                ).async_get_by_attempt_id(attempt_id)
                self._clear_retry(attempt_id)
                if lifecycle is None:
                    self._set_status(
                        record,
                        status=STATUS_MISSED,
                        now=now,
                        last_error="approved_start_window_missed_before_lifecycle_prepare",
                    )
                else:
                    self._set_status(
                        record,
                        status=STATUS_EXISTING_LIFECYCLE,
                        now=now,
                        last_error=f"lifecycle_state:{lifecycle.state}",
                        lifecycle_prepared=lifecycle.state == STATE_PREPARED,
                    )
                return

            self._set_status(record, status=STATUS_PREPARING, now=now)
            lifecycles = lifecycle_repository(self._hass, self._entry_id)
            lifecycle = await lifecycles.async_get_by_attempt_id(attempt_id)
            try:
                if lifecycle is None:
                    await async_prepare_execution_lifecycle(
                        self._hass,
                        entry_id=self._entry_id,
                        attempt_id=attempt_id,
                        plan_value=record.plan.as_dict(),
                        now=now,
                    )
                    lifecycle = await lifecycles.async_get_by_attempt_id(attempt_id)
                if lifecycle is None:
                    raise RuntimeError("lifecycle prepare returned without durable lifecycle")
                if lifecycle.state in _TERMINAL_LIFECYCLE_STATES:
                    self._clear_retry(attempt_id)
                    self._set_status(
                        record,
                        status=STATUS_EXISTING_LIFECYCLE,
                        now=now,
                        last_error=f"lifecycle_state:{lifecycle.state}",
                    )
                    return
                if lifecycle.state != STATE_PREPARED:
                    self._clear_retry(attempt_id)
                    self._set_status(
                        record,
                        status=STATUS_BLOCKED,
                        now=now,
                        last_error=f"unexpected_lifecycle_state:{lifecycle.state}",
                    )
                    return

                stop_result = await async_prepare_stop_lease(
                    self._hass,
                    entry_id=self._entry_id,
                    attempt_id=attempt_id,
                    now=now,
                )
                if stop_result.get("stop_obligation_armed") is not True:
                    raise RuntimeError("stop lease prepare did not arm durable stop obligation")
                if stop_result.get("service_call_performed") is not False:
                    raise RuntimeError("stop lease preparation unexpectedly reported service call")

                self._clear_retry(attempt_id)
                final_lifecycle = await lifecycles.async_get_by_attempt_id(attempt_id)
                if final_lifecycle is not None and final_lifecycle.state == STATE_PREPARED:
                    status = STATUS_PREPARED_WITH_STOP_LEASE
                else:
                    status = STATUS_DELEGATED
                self._set_status(
                    record,
                    status=status,
                    now=now,
                    lifecycle_prepared=True,
                    stop_lease_prepared=True,
                    retry_count=0,
                )
            except Exception as err:
                # Only a still-prepared lifecycle is eligible for a retry. If a
                # start crossed into dispatching/dispatched/recovery/verified,
                # this scheduler must never infer that repeating anything is safe.
                try:
                    latest = await lifecycles.async_get_by_attempt_id(attempt_id)
                except Exception:
                    latest = None
                if latest is None and await self._already_satisfied_after_prepare_rejection(
                    record,
                    now=now,
                ):
                    return
                if (
                    latest is not None
                    and latest.state == STATE_PREPARED
                    and self._schedule_stop_lease_retry(
                        record,
                        now=now,
                        grace_deadline=grace_deadline,
                        error=err,
                    )
                ):
                    return
                self._clear_retry(attempt_id)
                self._set_status(
                    record,
                    status=STATUS_ERROR,
                    now=now,
                    last_error=str(err),
                    lifecycle_prepared=(latest is not None and latest.state == STATE_PREPARED),
                )

    async def async_refresh(self, *, now: datetime | None = None) -> None:
        """Reconstruct exact start timers and process any currently-open window."""
        if not self._started:
            return
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        immediate: list[str] = []
        async with self._lock:
            self._cancel_all_timers()
            dependencies_ready, dependency_error = self._dependencies_ready()
            if not dependencies_ready:
                self._healthy = False
                self._last_error = dependency_error
                return
            self._healthy = True
            self._last_error = None
            records = await pending_run_repository(self._hass, self._entry_id).async_list()
            self._status_by_attempt = {}
            lifecycles = lifecycle_repository(self._hass, self._entry_id)
            live_attempts = {record.attempt_id for record in records}
            for attempt_id in tuple(self._retry_count_by_attempt):
                if attempt_id not in live_attempts:
                    self._clear_retry(attempt_id)

            for record in records:
                if not self._started:
                    return
                lifecycle = await lifecycles.async_get_by_attempt_id(record.attempt_id)
                starts_at = self._starts_at(record)
                grace_deadline = self._grace_deadline(record)

                if lifecycle is not None and lifecycle.state in _TERMINAL_LIFECYCLE_STATES:
                    self._clear_retry(record.attempt_id)
                    self._set_status(
                        record,
                        status=STATUS_EXISTING_LIFECYCLE,
                        now=current,
                        last_error=f"lifecycle_state:{lifecycle.state}",
                    )
                    continue

                if current > grace_deadline:
                    self._clear_retry(record.attempt_id)
                    self._set_status(
                        record,
                        status=(STATUS_EXISTING_LIFECYCLE if lifecycle is not None else STATUS_MISSED),
                        now=current,
                        last_error=(
                            f"lifecycle_state:{lifecycle.state}"
                            if lifecycle is not None
                            else "approved_start_window_missed_before_lifecycle_prepare"
                        ),
                        lifecycle_prepared=(lifecycle is not None and lifecycle.state == STATE_PREPARED),
                    )
                    continue

                if current < starts_at and lifecycle is None:
                    self._schedule_timer(record, starts_at)
                    self._set_status(
                        record,
                        status=STATUS_SCHEDULED,
                        now=current,
                        next_wake_at=starts_at.astimezone(timezone.utc).isoformat(),
                        timer_active=True,
                    )
                    continue

                immediate.append(record.attempt_id)

        for attempt_id in immediate:
            if not self._started:
                return
            await self._async_process(attempt_id, current)


def pending_run_scheduler(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionPendingRunScheduler:
    domain_data = hass.data.setdefault(DOMAIN, {})
    schedulers = domain_data.get(_RUNTIME_KEY)
    if not isinstance(schedulers, dict):
        schedulers = {}
        domain_data[_RUNTIME_KEY] = schedulers
    scheduler = schedulers.get(entry_id)
    if isinstance(scheduler, ExecutionPendingRunScheduler):
        return scheduler
    scheduler = ExecutionPendingRunScheduler(hass, entry_id)
    schedulers[entry_id] = scheduler
    return scheduler


async def async_start_pending_run_scheduler(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionPendingRunScheduler:
    scheduler = pending_run_scheduler(hass, entry_id)
    await scheduler.async_start()
    return scheduler


async def async_stop_pending_run_scheduler(hass: HomeAssistant, entry_id: str) -> None:
    await pending_run_scheduler(hass, entry_id).async_stop()


async def async_refresh_pending_run_scheduler_if_started(
    hass: HomeAssistant,
    entry_id: str,
) -> None:
    scheduler = pending_run_scheduler(hass, entry_id)
    if scheduler.started:
        await scheduler.async_refresh()
