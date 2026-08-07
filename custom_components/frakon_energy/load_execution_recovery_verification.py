"""Guarded audit-only recovery verification for FRAKON Energy.

This module can persist only a lifecycle verification transition after re-reading
the durable record, exact stop ownership and live Home Assistant entity state.
It never performs or retries a Home Assistant service call.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_lifecycle import (
    STATE_VERIFIED,
    VERIFY_CONFIRMED,
    ExecutionLifecycleError,
    verify_desired_state,
)
from .load_execution_lifecycle_recovery import assert_lifecycle_recovery_ready
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_execution_recovery_resolution import (
    RESOLUTION_SAFE_TO_VERIFY,
    evaluate_recovery_resolution,
)
from .load_execution_start_stop_ownership import async_start_stop_ownership_proof

_LOCKS_KEY = "load_execution_recovery_verification_locks_by_entry"


class RecoveryVerificationError(ValueError):
    """Raised when recovery verification cannot be safely persisted."""


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


def _live_state(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    return str(state.state) if state is not None else None


def _idempotent_verified_result(
    record: Any,
    *,
    current_state: str | None,
) -> dict[str, Any]:
    call_evidence = record.as_dict()["service_call_performed"]
    normalized = current_state.strip().lower() if isinstance(current_state, str) else None
    return {
        "lifecycle": record.as_dict(),
        "current_state": normalized,
        "desired_state_observed_now": normalized == record.desired_state,
        "verification_performed": False,
        "state_transition_performed": False,
        "idempotent_replay": True,
        "can_redispatch": False,
        "service_call_performed": call_evidence,
        "execution_performed": False,
        "executor_available": False,
    }


async def async_verify_recovery_lifecycle(
    hass: HomeAssistant,
    *,
    entry_id: str,
    attempt_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist only a verified observation; never dispatch or retry an action."""
    if not entry_id or not attempt_id:
        raise RecoveryVerificationError("entry_id and attempt_id are required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise RecoveryVerificationError("now must be timezone-aware")

    assert_lifecycle_recovery_ready(hass, entry_id)
    async with _transaction_lock(hass, entry_id):
        repository = lifecycle_repository(hass, entry_id)
        record = await repository.async_get_by_attempt_id(attempt_id)
        if record is None:
            raise RecoveryVerificationError(
                f"execution lifecycle not found: {attempt_id}"
            )

        live_state = _live_state(hass, record.entity_id)
        if record.state == STATE_VERIFIED and record.verification_status == VERIFY_CONFIRMED:
            return _idempotent_verified_result(
                record,
                current_state=live_state,
            )

        ownership = await async_start_stop_ownership_proof(
            hass,
            entry_id=entry_id,
            start=record,
        )
        decision = evaluate_recovery_resolution(
            record,
            current_state=live_state,
            stop_ownership_ready=ownership.ownership_ready,
        )
        if decision.status != RESOLUTION_SAFE_TO_VERIFY or not decision.can_mark_verified:
            raise RecoveryVerificationError(
                f"recovery verification is not safe: {decision.status}/{decision.reason}"
            )

        timestamp = max(int(current.timestamp()), record.updated_at)
        try:
            verified = verify_desired_state(
                record,
                current_state=live_state,
                now=timestamp,
            )
        except ExecutionLifecycleError as err:
            raise RecoveryVerificationError(str(err)) from err

        updated = await repository.async_update(verified)
        call_evidence = updated.as_dict()["service_call_performed"]
        return {
            "lifecycle": updated.as_dict(),
            "stop_ownership": ownership.as_dict(),
            "resolution": decision.as_dict(),
            "current_state": decision.current_state,
            "desired_state_observed_now": True,
            "verification_performed": True,
            "state_transition_performed": True,
            "idempotent_replay": False,
            "can_redispatch": False,
            "service_call_performed": call_evidence,
            "execution_performed": False,
            "executor_available": False,
        }
