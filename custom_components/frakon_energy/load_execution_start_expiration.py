"""Guarded terminal expiration for missed FRAKON Energy start windows.

A prepared bounded start whose immutable approved start/plan window has already
expired can never be executed safely. This transaction records that terminal
no-dispatch outcome. It performs no Home Assistant service call.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_bounded_dispatch_gate import BOUNDED_GATE_BLOCKED
from .load_execution_bounded_dispatch_gate_ws_api import async_bounded_dispatch_gate
from .load_execution_lifecycle import (
    CALL_NOT_STARTED,
    STATE_FAILED,
    STATE_PREPARED,
    ExecutionLifecycleError,
    mark_failed,
)
from .load_execution_lifecycle_recovery import assert_lifecycle_recovery_ready
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_execution_readiness import REASON_PLAN_EXPIRED, REASON_START_MISSED
from .load_execution_stop_lifecycle_runtime import stop_lifecycle_repository

_LOCKS_KEY = "load_execution_start_expiration_locks_by_entry"
EXPIRATION_PREFIX = "start_expired_no_dispatch:"
EXPIRABLE_START_REASONS = frozenset({REASON_START_MISSED, REASON_PLAN_EXPIRED})


class StartExpirationError(ValueError):
    """Raised when a prepared start is not safely terminal-expirable."""


def _transaction_lock(hass: HomeAssistant, entry_id: str) -> asyncio.Lock:
    domain_data = hass.data.setdefault(DOMAIN, {})
    locks = domain_data.get(_LOCKS_KEY)
    if not isinstance(locks, dict):
        locks = {}
        domain_data[_LOCKS_KEY] = locks
    lock = locks.get(entry_id)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        locks[entry_id] = lock
    return lock


def _aware_now(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise StartExpirationError("now must be timezone-aware")
    return current


def is_expiration_terminal(record: Any) -> bool:
    return (
        record.state == STATE_FAILED
        and record.service_call_status == CALL_NOT_STARTED
        and isinstance(record.failure_reason, str)
        and record.failure_reason.startswith(EXPIRATION_PREFIX)
    )


async def async_expire_prepared_start(
    hass: HomeAssistant,
    *,
    entry_id: str,
    attempt_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist failed/not-started only for an irreversibly expired prepared run."""
    if not entry_id or not attempt_id:
        raise StartExpirationError("entry_id and attempt_id are required")
    current = _aware_now(now)
    assert_lifecycle_recovery_ready(hass, entry_id)

    async with _transaction_lock(hass, entry_id):
        repository = lifecycle_repository(hass, entry_id)
        record = await repository.async_get_by_attempt_id(attempt_id)
        if record is None:
            raise StartExpirationError(f"execution lifecycle not found: {attempt_id}")
        record.validated()

        if is_expiration_terminal(record):
            return {
                "lifecycle": record.as_dict(),
                "expiration_performed": False,
                "state_transition_performed": False,
                "idempotent_replay": True,
                "service_call_performed": False,
                "execution_performed": False,
                "executor_available": False,
            }
        if record.state != STATE_PREPARED:
            raise StartExpirationError(
                f"only prepared lifecycle can expire without dispatch: {record.state}"
            )

        gate = await async_bounded_dispatch_gate(
            hass,
            entry_id=entry_id,
            attempt_id=attempt_id,
            now=current,
        )
        decision = gate.get("bounded_dispatch_gate")
        if not isinstance(decision, dict):
            raise StartExpirationError("bounded dispatch gate response is invalid")
        status = decision.get("status")
        reason = str(decision.get("reason", ""))
        if status != BOUNDED_GATE_BLOCKED or reason not in EXPIRABLE_START_REASONS:
            raise StartExpirationError(
                f"prepared lifecycle is not terminally expired: {status}/{reason}"
            )

        stop = await stop_lifecycle_repository(
            hass,
            entry_id,
        ).async_get_by_start_lifecycle_id(record.lifecycle_id)
        if stop is not None:
            raise StartExpirationError(
                "prepared lifecycle unexpectedly has durable stop ownership; refusing expiration"
            )

        unchanged = await repository.async_get_by_attempt_id(attempt_id)
        if unchanged != record:
            raise StartExpirationError("lifecycle changed during expiration gate evaluation")

        try:
            failed = mark_failed(
                record,
                reason=f"{EXPIRATION_PREFIX}{reason}",
                now=max(int(current.timestamp()), record.updated_at),
                service_call_status=CALL_NOT_STARTED,
            )
        except ExecutionLifecycleError as err:
            raise StartExpirationError(str(err)) from err
        persisted = await repository.async_update(failed)
        return {
            "lifecycle": persisted.as_dict(),
            "expiration_performed": True,
            "state_transition_performed": True,
            "idempotent_replay": False,
            "service_call_performed": False,
            "execution_performed": False,
            "executor_available": False,
        }
