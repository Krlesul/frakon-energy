"""Autonomous bounded-start runtime for already-approved FRAKON Energy work.

This scheduler creates no approval, attempt, lifecycle, action snapshot or stop
lease. It can only react to durable start lifecycles that are already prepared
by the existing approval flow. Physical execution is delegated to the isolated
crash-safe bounded start dispatcher and unknown outcomes are never retried.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_bounded_dispatch_gate import (
    BOUNDED_GATE_ALREADY_SATISFIED,
    BOUNDED_GATE_BLOCKED,
    BOUNDED_GATE_READY,
    REASON_STOP_LEASE_REQUIRED,
)
from .load_execution_bounded_dispatch_gate_ws_api import async_bounded_dispatch_gate
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
from .load_execution_noop_completion import NOOP_TERMINAL_REASON
from .load_execution_recovery_verification import async_verify_recovery_lifecycle
from .load_execution_start_dispatcher import (
    StartDispatchError,
    StartDispatchUnknownOutcomeError,
    async_dispatch_bounded_start,
)
from .load_execution_stop_recovery import STOP_RECOVERY_OK, stop_recovery_summary
from .load_execution_stop_scheduler import stop_scheduler

_RUNTIME_KEY = "load_execution_start_schedulers_by_entry"

STATUS_WAITING_STOP_LEASE = "waiting_for_stop_lease"
STATUS_STARTING = "starting"
STATUS_STARTED_VERIFIED = "started_verified"
STATUS_STARTED_PENDING_VERIFICATION = "started_pending_verification"
STATUS_ALREADY_SATISFIED = "already_satisfied"
STATUS_RECOVERY_REVIEW = "recovery_review"
STATUS_VERIFIED = "verified"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked"
STATUS_ERROR = "error"


@dataclass(frozen=True, slots=True)
class StartSchedulerStatus:
    attempt_id: str
    lifecycle_id: str
    entity_id: str
    status: str
    last_processed_at: str | None = None
    last_error: str | None = None
    physical_dispatch_attempted: bool = False
    service_call_performed: bool | None = False
    execution_performed: bool = False
    can_redispatch: bool = False
    executor_available: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExecutionStartScheduler:
    """Event-driven autonomous executor for already-prepared bounded starts."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        if not entry_id:
            raise ValueError("entry_id is required")
        self._hass = hass
        self._entry_id = entry_id
        self._lock = asyncio.Lock()
        self._started = False
        self._healthy = True
        self._last_error: str | None = None
        self._status_by_attempt: dict[str, StartSchedulerStatus] = {}

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

    def statuses(self) -> tuple[StartSchedulerStatus, ...]:
        return tuple(
            sorted(
                self._status_by_attempt.values(),
                key=lambda item: item.attempt_id,
            )
        )

    @staticmethod
    def _timestamp(now: datetime) -> str:
        return now.astimezone(timezone.utc).isoformat()

    def _dependencies_ready(self) -> tuple[bool, str | None]:
        start_recovery = lifecycle_recovery_summary(self._hass, self._entry_id)
        stop_recovery = stop_recovery_summary(self._hass, self._entry_id)
        stop_runtime = stop_scheduler(self._hass, self._entry_id)
        if start_recovery.status != RECOVERY_OK:
            return False, f"start_recovery:{start_recovery.status}"
        if stop_recovery.status != STOP_RECOVERY_OK:
            return False, f"stop_recovery:{stop_recovery.status}"
        if not stop_runtime.started or not stop_runtime.healthy:
            return False, "autonomous_stop_runtime_not_ready"
        return True, None

    async def async_start(self) -> None:
        if self._started:
            await self.async_refresh()
            return
        self._started = True
        try:
            await self.async_refresh()
        except Exception as err:
            self._healthy = False
            self._last_error = str(err)

    async def async_stop(self) -> None:
        self._started = False

    def _set_from_dispatch_result(
        self,
        *,
        attempt_id: str,
        lifecycle_id: str,
        entity_id: str,
        result: dict[str, Any],
        now: datetime,
    ) -> None:
        result_status = str(result.get("status", ""))
        if result_status in ("start_verified", "verified_without_redispatch", "already_verified"):
            status = STATUS_STARTED_VERIFIED
        elif result_status in ("already_satisfied_no_start", "already_completed"):
            status = STATUS_ALREADY_SATISFIED
        elif result_status in (
            "start_dispatched_pending_verification",
            "start_confirmed_verification_persistence_failed",
        ):
            status = STATUS_STARTED_PENDING_VERIFICATION
        else:
            status = STATUS_ERROR
        self._status_by_attempt[attempt_id] = StartSchedulerStatus(
            attempt_id=attempt_id,
            lifecycle_id=lifecycle_id,
            entity_id=entity_id,
            status=status,
            last_processed_at=self._timestamp(now),
            last_error=str(result.get("verification_error")) if result.get("verification_error") else None,
            physical_dispatch_attempted=result.get("physical_dispatch_attempted") is True,
            service_call_performed=result.get("service_call_performed"),
            execution_performed=result.get("execution_performed") is True,
            can_redispatch=False,
        )

    async def _process_prepared(self, record: Any, now: datetime) -> None:
        timestamp = self._timestamp(now)
        try:
            gate = await async_bounded_dispatch_gate(
                self._hass,
                entry_id=self._entry_id,
                attempt_id=record.attempt_id,
                now=now,
            )
            decision = gate.get("bounded_dispatch_gate")
            if not isinstance(decision, dict):
                raise ValueError("bounded dispatch gate response is invalid")
            status = decision.get("status")
            reason = str(decision.get("reason", ""))
            if status == BOUNDED_GATE_BLOCKED:
                runtime_status = (
                    STATUS_WAITING_STOP_LEASE
                    if reason == REASON_STOP_LEASE_REQUIRED
                    else STATUS_BLOCKED
                )
                self._status_by_attempt[record.attempt_id] = StartSchedulerStatus(
                    attempt_id=record.attempt_id,
                    lifecycle_id=record.lifecycle_id,
                    entity_id=record.entity_id,
                    status=runtime_status,
                    last_processed_at=timestamp,
                    last_error=None if runtime_status == STATUS_WAITING_STOP_LEASE else reason,
                )
                return
            if status not in (BOUNDED_GATE_READY, BOUNDED_GATE_ALREADY_SATISFIED):
                self._status_by_attempt[record.attempt_id] = StartSchedulerStatus(
                    attempt_id=record.attempt_id,
                    lifecycle_id=record.lifecycle_id,
                    entity_id=record.entity_id,
                    status=STATUS_BLOCKED,
                    last_processed_at=timestamp,
                    last_error=f"unexpected bounded gate status: {status}/{reason}",
                )
                return

            self._status_by_attempt[record.attempt_id] = StartSchedulerStatus(
                attempt_id=record.attempt_id,
                lifecycle_id=record.lifecycle_id,
                entity_id=record.entity_id,
                status=STATUS_STARTING,
                last_processed_at=timestamp,
            )
            result = await async_dispatch_bounded_start(
                self._hass,
                entry_id=self._entry_id,
                attempt_id=record.attempt_id,
                context=None,
                now=now,
            )
            self._set_from_dispatch_result(
                attempt_id=record.attempt_id,
                lifecycle_id=record.lifecycle_id,
                entity_id=record.entity_id,
                result=result,
                now=now,
            )
        except StartDispatchUnknownOutcomeError as err:
            self._status_by_attempt[record.attempt_id] = StartSchedulerStatus(
                attempt_id=record.attempt_id,
                lifecycle_id=record.lifecycle_id,
                entity_id=record.entity_id,
                status=STATUS_RECOVERY_REVIEW,
                last_processed_at=timestamp,
                last_error=str(err),
                physical_dispatch_attempted=True,
                service_call_performed=None,
                execution_performed=False,
                can_redispatch=False,
            )
        except StartDispatchError as err:
            self._status_by_attempt[record.attempt_id] = StartSchedulerStatus(
                attempt_id=record.attempt_id,
                lifecycle_id=record.lifecycle_id,
                entity_id=record.entity_id,
                status=STATUS_BLOCKED,
                last_processed_at=timestamp,
                last_error=str(err),
            )
        except Exception as err:
            self._status_by_attempt[record.attempt_id] = StartSchedulerStatus(
                attempt_id=record.attempt_id,
                lifecycle_id=record.lifecycle_id,
                entity_id=record.entity_id,
                status=STATUS_ERROR,
                last_processed_at=timestamp,
                last_error=str(err),
            )

    async def _process_recovery(self, record: Any, now: datetime) -> None:
        timestamp = self._timestamp(now)
        try:
            result = await async_verify_recovery_lifecycle(
                self._hass,
                entry_id=self._entry_id,
                attempt_id=record.attempt_id,
                now=now,
            )
        except Exception as err:
            self._status_by_attempt[record.attempt_id] = StartSchedulerStatus(
                attempt_id=record.attempt_id,
                lifecycle_id=record.lifecycle_id,
                entity_id=record.entity_id,
                status=STATUS_RECOVERY_REVIEW,
                last_processed_at=timestamp,
                last_error=str(err),
                physical_dispatch_attempted=record.dispatch_attempts > 0,
                service_call_performed=record.as_dict()["service_call_performed"],
                execution_performed=False,
                can_redispatch=False,
            )
            return
        self._status_by_attempt[record.attempt_id] = StartSchedulerStatus(
            attempt_id=record.attempt_id,
            lifecycle_id=record.lifecycle_id,
            entity_id=record.entity_id,
            status=STATUS_VERIFIED,
            last_processed_at=timestamp,
            physical_dispatch_attempted=False,
            service_call_performed=result.get("service_call_performed"),
            execution_performed=False,
            can_redispatch=False,
        )

    async def async_refresh(self, *, now: datetime | None = None) -> None:
        """Scan durable start lifecycles and execute only already-prepared work."""
        if not self._started:
            return
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        async with self._lock:
            dependencies_ready, reason = self._dependencies_ready()
            if not dependencies_ready:
                self._healthy = False
                self._last_error = reason
                return
            self._healthy = True
            self._last_error = None
            records = await lifecycle_repository(self._hass, self._entry_id).async_list()
            new_status: dict[str, StartSchedulerStatus] = {}
            self._status_by_attempt = new_status

            for record in records:
                if not self._started:
                    return
                if record.state == STATE_PREPARED:
                    await self._process_prepared(record, current)
                    continue
                if record.state in (STATE_RECOVERY_REQUIRED, STATE_DISPATCHED):
                    await self._process_recovery(record, current)
                    continue
                if record.state == STATE_DISPATCHING:
                    self._status_by_attempt[record.attempt_id] = StartSchedulerStatus(
                        attempt_id=record.attempt_id,
                        lifecycle_id=record.lifecycle_id,
                        entity_id=record.entity_id,
                        status=STATUS_RECOVERY_REVIEW,
                        last_processed_at=self._timestamp(current),
                        last_error="start outcome is unknown/in-flight; redispatch forbidden",
                        physical_dispatch_attempted=True,
                        service_call_performed=None,
                        can_redispatch=False,
                    )
                    continue
                if record.state == STATE_VERIFIED:
                    self._status_by_attempt[record.attempt_id] = StartSchedulerStatus(
                        attempt_id=record.attempt_id,
                        lifecycle_id=record.lifecycle_id,
                        entity_id=record.entity_id,
                        status=STATUS_VERIFIED,
                        service_call_performed=record.as_dict()["service_call_performed"],
                        can_redispatch=False,
                    )
                    continue
                if record.state == STATE_CANCELLED and record.failure_reason == NOOP_TERMINAL_REASON:
                    status = STATUS_ALREADY_SATISFIED
                elif record.state == STATE_FAILED:
                    status = STATUS_FAILED
                else:
                    status = STATUS_BLOCKED
                self._status_by_attempt[record.attempt_id] = StartSchedulerStatus(
                    attempt_id=record.attempt_id,
                    lifecycle_id=record.lifecycle_id,
                    entity_id=record.entity_id,
                    status=status,
                    service_call_performed=record.as_dict()["service_call_performed"],
                    can_redispatch=False,
                )


def _scheduler_map(hass: HomeAssistant) -> dict[str, ExecutionStartScheduler]:
    domain_data = hass.data.setdefault(DOMAIN, {})
    value = domain_data.get(_RUNTIME_KEY)
    if not isinstance(value, dict):
        value = {}
        domain_data[_RUNTIME_KEY] = value
    return value


def start_scheduler(hass: HomeAssistant, entry_id: str) -> ExecutionStartScheduler:
    schedulers = _scheduler_map(hass)
    scheduler = schedulers.get(entry_id)
    if isinstance(scheduler, ExecutionStartScheduler):
        return scheduler
    scheduler = ExecutionStartScheduler(hass, entry_id)
    schedulers[entry_id] = scheduler
    return scheduler


async def async_start_start_scheduler(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionStartScheduler:
    scheduler = start_scheduler(hass, entry_id)
    await scheduler.async_start()
    return scheduler


async def async_refresh_start_scheduler_if_started(
    hass: HomeAssistant,
    entry_id: str,
) -> None:
    scheduler = _scheduler_map(hass).get(entry_id)
    if isinstance(scheduler, ExecutionStartScheduler) and scheduler.started:
        try:
            await scheduler.async_refresh()
        except Exception as err:
            scheduler._healthy = False
            scheduler._last_error = str(err)


async def async_stop_start_scheduler(hass: HomeAssistant, entry_id: str) -> None:
    scheduler = _scheduler_map(hass).pop(entry_id, None)
    if isinstance(scheduler, ExecutionStartScheduler):
        await scheduler.async_stop()
