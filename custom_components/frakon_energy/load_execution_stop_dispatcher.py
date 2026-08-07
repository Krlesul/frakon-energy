"""Crash-safe physical stop dispatcher for bounded FRAKON Energy execution.

This is the first execution layer allowed to call a Home Assistant service. It
can only invoke the immutable allowlisted stop action already persisted in the
stop lifecycle. It never starts a load and never automatically retries an
unknown stop outcome.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import Context, HomeAssistant

from .const import DOMAIN
from .load_execution_stop_due_gate import (
    STOP_DUE_ALREADY_OFF,
    STOP_DUE_READY,
    STOP_DUE_SAFE_TO_VERIFY,
    evaluate_stop_due_gate,
)
from .load_execution_stop_lifecycle import (
    STOP_STATE_DISPATCHED,
    STOP_STATE_DISPATCHING,
    STOP_STATE_RECOVERY_REQUIRED,
    STOP_STATE_SATISFIED,
    STOP_STATE_VERIFIED,
    ExecutionStopLifecycleRecord,
    StopLifecycleError,
    confirm_stop_dispatch,
    require_stop_recovery_after_restart,
    verify_stop_state,
)
from .load_execution_stop_lifecycle_runtime import stop_lifecycle_repository
from .load_execution_stop_recovery import assert_stop_recovery_ready
from .load_execution_stop_resolution import async_complete_stop_noop
from .load_execution_stop_scheduler import async_refresh_stop_scheduler_if_started
from .load_execution_stop_transition_guard import begin_due_stop_dispatch

_DISPATCH_LOCKS_KEY = "load_execution_physical_stop_dispatch_locks_by_entry"


class StopDispatchError(RuntimeError):
    """Raised when a physical stop cannot be safely dispatched."""


class StopDispatchUnknownOutcomeError(StopDispatchError):
    """Raised after a service-call boundary whose physical outcome is uncertain."""


def _transaction_lock(hass: HomeAssistant, entry_id: str) -> asyncio.Lock:
    domain_data = hass.data.setdefault(DOMAIN, {})
    locks = domain_data.get(_DISPATCH_LOCKS_KEY)
    if not isinstance(locks, dict):
        locks = {}
        domain_data[_DISPATCH_LOCKS_KEY] = locks
    lock = locks.get(entry_id)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        locks[entry_id] = lock
    return lock


def _aware_now(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise StopDispatchError("now must be timezone-aware")
    return current


def _live_state(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    return str(state.state) if state is not None else None


def _service_call_performed(record: ExecutionStopLifecycleRecord) -> bool | None:
    return record.as_dict()["service_call_performed"]


def _replay_payload(
    record: ExecutionStopLifecycleRecord,
    *,
    status: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "stop_lifecycle": record.as_dict(),
        "idempotent_replay": True,
        "physical_dispatch_attempted": record.dispatch_attempts > 0,
        "service_call_performed": _service_call_performed(record),
        "execution_performed": False,
        "state_transition_performed": False,
        "executor_available": True,
        "can_retry_unknown": False,
    }


async def _refresh_scheduler(hass: HomeAssistant, entry_id: str) -> None:
    """Best-effort scheduler refresh; never widens stop authority."""
    await async_refresh_stop_scheduler_if_started(hass, entry_id)


async def _persist_unknown_recovery(
    hass: HomeAssistant,
    *,
    entry_id: str,
    record: ExecutionStopLifecycleRecord,
    now_ts: int,
) -> Exception | None:
    """Best-effort durable conversion from dispatching to unknown recovery."""
    try:
        recovered = require_stop_recovery_after_restart(
            record,
            now=max(now_ts, record.updated_at),
        )
        await stop_lifecycle_repository(hass, entry_id).async_update(recovered)
        await _refresh_scheduler(hass, entry_id)
        return None
    except Exception as err:  # The already persisted dispatching record remains fail-closed.
        return err


async def async_dispatch_due_stop(
    hass: HomeAssistant,
    *,
    entry_id: str,
    start_lifecycle_id: str,
    context: Context | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Perform at most one immutable physical turn-off for a due stop lifecycle."""
    if not entry_id or not start_lifecycle_id:
        raise StopDispatchError("entry_id and start_lifecycle_id are required")
    current = _aware_now(now)
    now_ts = int(current.timestamp())
    assert_stop_recovery_ready(hass, entry_id)

    async with _transaction_lock(hass, entry_id):
        repository = stop_lifecycle_repository(hass, entry_id)
        record = await repository.async_get_by_start_lifecycle_id(start_lifecycle_id)
        if record is None:
            raise StopDispatchError(f"stop lifecycle not found: {start_lifecycle_id}")
        record.validated()

        if record.state == STOP_STATE_SATISFIED:
            return _replay_payload(record, status="already_satisfied_without_dispatch")
        if record.state == STOP_STATE_VERIFIED:
            return _replay_payload(record, status="already_verified")
        if record.state == STOP_STATE_DISPATCHING:
            raise StopDispatchUnknownOutcomeError(
                "stop lifecycle is dispatching with an uncertain outcome; automatic retry is forbidden"
            )

        live_state = _live_state(hass, record.entity_id)
        decision = evaluate_stop_due_gate(
            record=record,
            current_state=live_state,
            now=current,
            recovery_ready=True,
        )

        if record.state in (STOP_STATE_RECOVERY_REQUIRED, STOP_STATE_DISPATCHED):
            if decision.status == STOP_DUE_SAFE_TO_VERIFY and decision.can_mark_verified:
                verified = verify_stop_state(
                    record,
                    current_state=live_state,
                    now=max(now_ts, record.updated_at),
                )
                persisted = await repository.async_update(verified)
                await _refresh_scheduler(hass, entry_id)
                return {
                    "status": "verified_without_redispatch",
                    "stop_lifecycle": persisted.as_dict(),
                    "idempotent_replay": False,
                    "physical_dispatch_attempted": False,
                    "service_call_performed": _service_call_performed(persisted),
                    "execution_performed": False,
                    "state_transition_performed": True,
                    "executor_available": True,
                    "can_retry_unknown": False,
                }
            raise StopDispatchError(
                f"existing stop dispatch cannot be retried: {decision.status}/{decision.reason}"
            )

        if decision.status == STOP_DUE_ALREADY_OFF and decision.can_complete_noop:
            # Delegate to the independently guarded no-dispatch transaction. It rechecks
            # live state and the deadline before persistence and performs no service call.
            result = await async_complete_stop_noop(
                hass,
                entry_id=entry_id,
                start_lifecycle_id=start_lifecycle_id,
                now=current,
            )
            await _refresh_scheduler(hass, entry_id)
            return {
                "status": "already_off_no_dispatch",
                **result,
                "physical_dispatch_attempted": False,
                "executor_available": True,
                "can_retry_unknown": False,
            }

        if decision.status != STOP_DUE_READY or not decision.can_dispatch_stop:
            raise StopDispatchError(
                f"stop is not ready for physical dispatch: {decision.status}/{decision.reason}"
            )

        unchanged = await repository.async_get_by_start_lifecycle_id(start_lifecycle_id)
        if unchanged != record:
            raise StopDispatchError("stop lifecycle changed during dispatch gate evaluation")

        # Persist unknown-outcome evidence BEFORE crossing the physical service boundary.
        dispatching = begin_due_stop_dispatch(
            record,
            now=max(now_ts, record.updated_at),
        )
        persisted_dispatching = await repository.async_update(dispatching)
        await _refresh_scheduler(hass, entry_id)

        try:
            await hass.services.async_call(
                persisted_dispatching.service_domain,
                persisted_dispatching.service_name,
                {},
                blocking=True,
                context=context,
                target={"entity_id": persisted_dispatching.entity_id},
            )
        except Exception as call_err:
            recovery_err = await _persist_unknown_recovery(
                hass,
                entry_id=entry_id,
                record=persisted_dispatching,
                now_ts=now_ts,
            )
            detail = (
                f"; recovery persistence also failed: {recovery_err}"
                if recovery_err is not None
                else ""
            )
            raise StopDispatchUnknownOutcomeError(
                f"physical stop call outcome is unknown: {call_err}{detail}"
            ) from call_err

        # Normal return from a blocking Home Assistant service call is confirmed call
        # evidence, but it is not yet proof that the entity reached `off`.
        confirmed = confirm_stop_dispatch(
            persisted_dispatching,
            now=max(now_ts, persisted_dispatching.updated_at),
        )
        try:
            persisted_confirmed = await repository.async_update(confirmed)
        except Exception as persist_err:
            recovery_err = await _persist_unknown_recovery(
                hass,
                entry_id=entry_id,
                record=persisted_dispatching,
                now_ts=now_ts,
            )
            detail = (
                f"; recovery persistence also failed: {recovery_err}"
                if recovery_err is not None
                else ""
            )
            raise StopDispatchUnknownOutcomeError(
                "physical stop call returned normally, but confirmed evidence could not "
                f"be persisted: {persist_err}{detail}"
            ) from persist_err

        await _refresh_scheduler(hass, entry_id)
        observed_state = _live_state(hass, persisted_confirmed.entity_id)
        normalized = observed_state.strip().lower() if isinstance(observed_state, str) else None
        if normalized == persisted_confirmed.desired_state:
            verified = verify_stop_state(
                persisted_confirmed,
                current_state=observed_state,
                now=max(now_ts, persisted_confirmed.updated_at),
            )
            try:
                persisted_verified = await repository.async_update(verified)
            except Exception as verify_persist_err:
                await _refresh_scheduler(hass, entry_id)
                return {
                    "status": "stop_confirmed_verification_persistence_failed",
                    "stop_lifecycle": persisted_confirmed.as_dict(),
                    "current_state": normalized,
                    "verification_error": str(verify_persist_err),
                    "idempotent_replay": False,
                    "physical_dispatch_attempted": True,
                    "service_call_performed": True,
                    "execution_performed": True,
                    "state_transition_performed": True,
                    "executor_available": True,
                    "can_retry_unknown": False,
                }
            await _refresh_scheduler(hass, entry_id)
            return {
                "status": "stop_verified",
                "stop_lifecycle": persisted_verified.as_dict(),
                "current_state": normalized,
                "idempotent_replay": False,
                "physical_dispatch_attempted": True,
                "service_call_performed": True,
                "execution_performed": True,
                "state_transition_performed": True,
                "executor_available": True,
                "can_retry_unknown": False,
            }

        return {
            "status": "stop_dispatched_pending_verification",
            "stop_lifecycle": persisted_confirmed.as_dict(),
            "current_state": normalized,
            "idempotent_replay": False,
            "physical_dispatch_attempted": True,
            "service_call_performed": True,
            "execution_performed": True,
            "state_transition_performed": True,
            "executor_available": True,
            "can_retry_unknown": False,
        }
