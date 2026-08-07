"""Guarded audit-only stop lifecycle resolution transactions.

These transactions may persist only no-dispatch satisfaction or observed-state
verification. They never call Home Assistant services and never retry a stop.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_stop_due_gate import (
    STOP_DUE_ALREADY_OFF,
    STOP_DUE_SAFE_TO_VERIFY,
    evaluate_stop_due_gate,
)
from .load_execution_stop_lifecycle import (
    STOP_STATE_SATISFIED,
    STOP_STATE_VERIFIED,
    ExecutionStopLifecycleRecord,
    StopLifecycleError,
    satisfy_stop_without_dispatch,
    verify_stop_state,
)
from .load_execution_stop_lifecycle_runtime import stop_lifecycle_repository
from .load_execution_stop_recovery import assert_stop_recovery_ready

_LOCKS_KEY = "load_execution_stop_resolution_locks_by_entry"


class StopResolutionError(ValueError):
    """Raised when a stop lifecycle cannot be safely resolved."""


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


def _live_state(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    return str(state.state) if state is not None else None


def _replay_payload(record: ExecutionStopLifecycleRecord) -> dict[str, Any]:
    return {
        "stop_lifecycle": record.as_dict(),
        "created": False,
        "idempotent_replay": True,
        "resolution_performed": False,
        "state_transition_performed": False,
        "service_call_performed": record.as_dict()["service_call_performed"],
        "execution_performed": False,
        "executor_available": False,
    }


async def async_complete_stop_noop(
    hass: HomeAssistant,
    *,
    entry_id: str,
    start_lifecycle_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist `satisfied` only when the due entity is already off."""
    if not entry_id or not start_lifecycle_id:
        raise StopResolutionError("entry_id and start_lifecycle_id are required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise StopResolutionError("now must be timezone-aware")
    assert_stop_recovery_ready(hass, entry_id)

    async with _transaction_lock(hass, entry_id):
        repository = stop_lifecycle_repository(hass, entry_id)
        record = await repository.async_get_by_start_lifecycle_id(start_lifecycle_id)
        if record is None:
            raise StopResolutionError(f"stop lifecycle not found: {start_lifecycle_id}")
        if record.state == STOP_STATE_SATISFIED:
            return _replay_payload(record)
        if record.state == STOP_STATE_VERIFIED:
            raise StopResolutionError("stop lifecycle is already verified, not a no-op completion")

        live_state = _live_state(hass, record.entity_id)
        decision = evaluate_stop_due_gate(
            record=record,
            current_state=live_state,
            now=current,
            recovery_ready=True,
        )
        if decision.status != STOP_DUE_ALREADY_OFF or not decision.can_complete_noop:
            raise StopResolutionError(
                f"stop no-op completion is not allowed: {decision.status}/{decision.reason}"
            )

        unchanged = await repository.async_get_by_start_lifecycle_id(start_lifecycle_id)
        if unchanged != record:
            raise StopResolutionError("stop lifecycle changed during no-op resolution")
        updated = satisfy_stop_without_dispatch(
            record,
            current_state=live_state,
            now=max(int(current.timestamp()), record.updated_at),
        )
        persisted = await repository.async_update(updated)
        return {
            "stop_lifecycle": persisted.as_dict(),
            "created": False,
            "idempotent_replay": False,
            "resolution_performed": True,
            "state_transition_performed": True,
            "service_call_performed": False,
            "execution_performed": False,
            "executor_available": False,
        }


async def async_verify_stop_resolution(
    hass: HomeAssistant,
    *,
    entry_id: str,
    start_lifecycle_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist `verified` only after the due gate re-observes desired state off."""
    if not entry_id or not start_lifecycle_id:
        raise StopResolutionError("entry_id and start_lifecycle_id are required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise StopResolutionError("now must be timezone-aware")
    assert_stop_recovery_ready(hass, entry_id)

    async with _transaction_lock(hass, entry_id):
        repository = stop_lifecycle_repository(hass, entry_id)
        record = await repository.async_get_by_start_lifecycle_id(start_lifecycle_id)
        if record is None:
            raise StopResolutionError(f"stop lifecycle not found: {start_lifecycle_id}")
        if record.state == STOP_STATE_VERIFIED:
            return _replay_payload(record)
        if record.state == STOP_STATE_SATISFIED:
            raise StopResolutionError("stop lifecycle was satisfied without dispatch")

        live_state = _live_state(hass, record.entity_id)
        decision = evaluate_stop_due_gate(
            record=record,
            current_state=live_state,
            now=current,
            recovery_ready=True,
        )
        if decision.status != STOP_DUE_SAFE_TO_VERIFY or not decision.can_mark_verified:
            raise StopResolutionError(
                f"stop verification is not allowed: {decision.status}/{decision.reason}"
            )

        unchanged = await repository.async_get_by_start_lifecycle_id(start_lifecycle_id)
        if unchanged != record:
            raise StopResolutionError("stop lifecycle changed during verification")
        updated = verify_stop_state(
            record,
            current_state=live_state,
            now=max(int(current.timestamp()), record.updated_at),
        )
        persisted = await repository.async_update(updated)
        return {
            "stop_lifecycle": persisted.as_dict(),
            "created": False,
            "idempotent_replay": False,
            "resolution_performed": True,
            "state_transition_performed": True,
            "service_call_performed": persisted.as_dict()["service_call_performed"],
            "execution_performed": False,
            "executor_available": False,
        }
