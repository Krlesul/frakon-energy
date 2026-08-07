"""Durable no-dispatch completion for already-satisfied FRAKON Energy loads.

This transaction terminally closes a prepared lifecycle only when the final
read-only dispatch gate says the immutable desired state is already satisfied.
It never performs a Home Assistant service call.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_dispatch_gate import DISPATCH_GATE_ALREADY_SATISFIED
from .load_execution_dispatch_gate_ws_api import async_execution_dispatch_gate
from .load_execution_lifecycle import (
    CALL_NOT_STARTED,
    STATE_CANCELLED,
    STATE_PREPARED,
    VERIFY_PENDING,
    ExecutionLifecycleError,
    cancel_prepared,
)
from .load_execution_lifecycle_recovery import assert_lifecycle_recovery_ready
from .load_execution_lifecycle_runtime import lifecycle_repository

NOOP_TERMINAL_REASON = "already_satisfied_no_dispatch"
_LOCKS_KEY = "load_execution_noop_completion_locks_by_entry"


class NoopCompletionError(ValueError):
    """Raised when a lifecycle cannot be safely completed without dispatch."""


def _transaction_lock(hass: HomeAssistant, entry_id: str) -> asyncio.Lock:
    domain_data = hass.data.setdefault(DOMAIN, {})
    locks = domain_data.get(_LOCKS_KEY)
    if not isinstance(locks, dict):
        locks = {}
        domain_data[_LOCKS_KEY] = locks
    lock = locks.get(entry_id)
    if isinstance(lock, asyncio.Lock):
        return lock
    lock = asyncio.Lock()
    locks[entry_id] = lock
    return lock


def _is_completed_noop(record: Any) -> bool:
    return (
        record.state == STATE_CANCELLED
        and record.service_call_status == CALL_NOT_STARTED
        and record.verification_status == VERIFY_PENDING
        and record.failure_reason == NOOP_TERMINAL_REASON
        and record.dispatch_attempts == 0
    )


def _replay_result(record: Any) -> dict[str, Any]:
    return {
        "lifecycle": record.as_dict(),
        "noop_completed": True,
        "terminal_reason": NOOP_TERMINAL_REASON,
        "state_transition_performed": False,
        "idempotent_replay": True,
        "can_dispatch": False,
        "can_redispatch": False,
        "service_call_performed": False,
        "execution_performed": False,
        "executor_available": False,
    }


async def async_complete_already_satisfied_noop(
    hass: HomeAssistant,
    *,
    entry_id: str,
    attempt_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Terminally close one prepared lifecycle without dispatching a service."""
    if not entry_id or not attempt_id:
        raise NoopCompletionError("entry_id and attempt_id are required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise NoopCompletionError("now must be timezone-aware")

    assert_lifecycle_recovery_ready(hass, entry_id)
    async with _transaction_lock(hass, entry_id):
        repository = lifecycle_repository(hass, entry_id)
        existing = await repository.async_get_by_attempt_id(attempt_id)
        if existing is None:
            raise NoopCompletionError(f"execution lifecycle not found: {attempt_id}")
        if _is_completed_noop(existing):
            return _replay_result(existing)
        if existing.state != STATE_PREPARED:
            raise NoopCompletionError(
                f"lifecycle is not prepared for no-op completion: {existing.state}"
            )

        gate_payload = await async_execution_dispatch_gate(
            hass,
            entry_id=entry_id,
            attempt_id=attempt_id,
            now=current,
        )
        gate = gate_payload.get("dispatch_gate")
        if not isinstance(gate, dict):
            raise NoopCompletionError("dispatch gate response is invalid")
        if gate.get("status") != DISPATCH_GATE_ALREADY_SATISFIED:
            raise NoopCompletionError(
                f"dispatch gate does not allow no-op completion: {gate.get('status')}/{gate.get('reason')}"
            )
        if gate.get("can_dispatch") is not False:
            raise NoopCompletionError("already-satisfied gate unexpectedly permits dispatch")

        # Re-read after the gate evaluation before applying the terminal transition.
        record = await repository.async_get_by_attempt_id(attempt_id)
        if record is None or record.state != STATE_PREPARED:
            raise NoopCompletionError("lifecycle changed during no-op completion")

        timestamp = max(int(current.timestamp()), record.updated_at)
        cancelled = cancel_prepared(record, now=timestamp)
        completed = replace(cancelled, failure_reason=NOOP_TERMINAL_REASON).validated()
        updated = await repository.async_update(completed)
        return {
            "lifecycle": updated.as_dict(),
            "dispatch_gate": gate,
            "noop_completed": True,
            "terminal_reason": NOOP_TERMINAL_REASON,
            "state_transition_performed": True,
            "idempotent_replay": False,
            "can_dispatch": False,
            "can_redispatch": False,
            "service_call_performed": False,
            "execution_performed": False,
            "executor_available": False,
        }
