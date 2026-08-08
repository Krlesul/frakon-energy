"""Crash-safe bounded physical start dispatcher for FRAKON Energy.

This is the first execution path allowed to turn a bounded load on. It can cross
that physical boundary only after the final bounded gate passes, the persistent
execution interlock is ARMED, the autonomous stop runtime is healthy, start
``dispatching`` is durable, and the exact stop lifecycle ownership has already
been persisted and proven cross-store.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import Context, HomeAssistant

from .const import DOMAIN
from .load_execution_arm import (
    ExecutionArmError,
    ExecutionDisarmedError,
    async_require_execution_armed,
    execution_arm_guard,
)
from .load_execution_bounded_dispatch_gate import (
    BOUNDED_GATE_ALREADY_SATISFIED,
    BOUNDED_GATE_READY,
    BoundedDispatchDecision,
)
from .load_execution_bounded_dispatch_gate_ws_api import async_bounded_dispatch_gate
from .load_execution_lifecycle import (
    CALL_NOT_STARTED,
    STATE_CANCELLED,
    STATE_DISPATCHED,
    STATE_DISPATCHING,
    STATE_FAILED,
    STATE_RECOVERY_REQUIRED,
    STATE_VERIFIED,
    ExecutionLifecycleRecord,
    begin_dispatch,
    confirm_dispatch,
    mark_failed,
    require_recovery_after_restart,
    verify_desired_state,
)
from .load_execution_lifecycle_recovery import assert_lifecycle_recovery_ready
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_execution_noop_completion import (
    NOOP_TERMINAL_REASON,
    async_complete_already_satisfied_noop,
)
from .load_execution_recovery_verification import async_verify_recovery_lifecycle
from .load_execution_start_stop_ownership import async_start_stop_ownership_proof
from .load_execution_stop_lease import ExecutionStopLease
from .load_execution_stop_lifecycle import (
    STOP_STATE_OWNED,
    ExecutionStopLifecycleRecord,
    fail_stop_lifecycle,
)
from .load_execution_stop_lifecycle_runtime import stop_lifecycle_repository
from .load_execution_stop_recovery import assert_stop_recovery_ready
from .load_execution_stop_scheduler import (
    async_refresh_stop_scheduler_if_started,
    stop_scheduler,
)

_START_DISPATCH_LOCKS_KEY = "load_execution_physical_start_dispatch_locks_by_entry"


class StartDispatchError(RuntimeError):
    """Raised when a bounded physical start cannot be safely dispatched."""


class StartDispatchUnknownOutcomeError(StartDispatchError):
    """Raised after a physical start boundary whose outcome is uncertain."""


def _transaction_lock(hass: HomeAssistant, entry_id: str) -> asyncio.Lock:
    domain_data = hass.data.setdefault(DOMAIN, {})
    locks = domain_data.get(_START_DISPATCH_LOCKS_KEY)
    if not isinstance(locks, dict):
        locks = {}
        domain_data[_START_DISPATCH_LOCKS_KEY] = locks
    lock = locks.get(entry_id)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        locks[entry_id] = lock
    return lock


def _aware_now(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise StartDispatchError("now must be timezone-aware")
    return current


def _live_state(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    return str(state.state) if state is not None else None


def _service_call_performed(record: ExecutionLifecycleRecord) -> bool | None:
    return record.as_dict()["service_call_performed"]


def _scheduler_ready(hass: HomeAssistant, entry_id: str) -> bool:
    scheduler = stop_scheduler(hass, entry_id)
    return scheduler.started and scheduler.healthy


def _terminal_replay(record: ExecutionLifecycleRecord) -> dict[str, Any]:
    return {
        "status": "already_verified" if record.state == STATE_VERIFIED else "already_completed",
        "lifecycle": record.as_dict(),
        "idempotent_replay": True,
        "physical_dispatch_attempted": record.dispatch_attempts > 0,
        "service_call_performed": _service_call_performed(record),
        "execution_performed": False,
        "state_transition_performed": False,
        "can_redispatch": False,
        "executor_available": True,
    }


async def _persist_start_unknown_recovery(
    hass: HomeAssistant,
    *,
    entry_id: str,
    record: ExecutionLifecycleRecord,
    now_ts: int,
) -> Exception | None:
    try:
        recovered = require_recovery_after_restart(
            record,
            now=max(now_ts, record.updated_at),
        )
        await lifecycle_repository(hass, entry_id).async_update(recovered)
        return None
    except Exception as err:
        # Persisted dispatching remains an explicit unknown outcome and startup
        # recovery will fail closed even if this secondary write also fails.
        return err


async def _abort_before_start_call(
    hass: HomeAssistant,
    *,
    entry_id: str,
    start_dispatching: ExecutionLifecycleRecord,
    stop_owned: ExecutionStopLifecycleRecord | None,
    reason: str,
    now_ts: int,
) -> tuple[Exception | None, Exception | None]:
    """Best-effort terminalize pre-call state without ever crossing service boundary."""
    stop_error: Exception | None = None
    start_error: Exception | None = None
    stop_terminal = stop_owned is None
    if stop_owned is not None:
        try:
            failed_stop = fail_stop_lifecycle(
                stop_owned,
                reason=reason,
                now=max(now_ts, stop_owned.updated_at),
            )
            await stop_lifecycle_repository(hass, entry_id).async_update(failed_stop)
            stop_terminal = True
        except Exception as err:
            stop_error = err

    # Mark the start definitely not-started only when there is no active stop
    # ownership left. If stop terminalization failed, keep start dispatching/unknown
    # so recovery remains conservative instead of claiming a clean abort.
    if stop_terminal:
        try:
            failed_start = mark_failed(
                start_dispatching,
                reason=reason,
                now=max(now_ts, start_dispatching.updated_at),
                service_call_status=CALL_NOT_STARTED,
            )
            await lifecycle_repository(hass, entry_id).async_update(failed_start)
        except Exception as err:
            start_error = err
    return stop_error, start_error


async def async_dispatch_bounded_start(
    hass: HomeAssistant,
    *,
    entry_id: str,
    attempt_id: str,
    context: Context | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Perform at most one immutable bounded physical start."""
    if not entry_id or not attempt_id:
        raise StartDispatchError("entry_id and attempt_id are required")
    current = _aware_now(now)
    now_ts = int(current.timestamp())
    assert_lifecycle_recovery_ready(hass, entry_id)
    assert_stop_recovery_ready(hass, entry_id)
    if not _scheduler_ready(hass, entry_id):
        raise StartDispatchError("autonomous stop scheduler is not started and healthy")

    async with _transaction_lock(hass, entry_id):
        start_repository = lifecycle_repository(hass, entry_id)
        existing = await start_repository.async_get_by_attempt_id(attempt_id)
        if existing is None:
            raise StartDispatchError(f"execution lifecycle not found: {attempt_id}")
        existing.validated()

        if existing.state == STATE_VERIFIED:
            return _terminal_replay(existing)
        if existing.state == STATE_CANCELLED and existing.failure_reason == NOOP_TERMINAL_REASON:
            return _terminal_replay(existing)
        if existing.state == STATE_DISPATCHING:
            raise StartDispatchUnknownOutcomeError(
                "start lifecycle is dispatching with an uncertain outcome; automatic retry is forbidden"
            )
        if existing.state in (STATE_RECOVERY_REQUIRED, STATE_DISPATCHED):
            try:
                result = await async_verify_recovery_lifecycle(
                    hass,
                    entry_id=entry_id,
                    attempt_id=attempt_id,
                    now=current,
                )
            except Exception as err:
                raise StartDispatchError(
                    f"existing start dispatch cannot be retried: {err}"
                ) from err
            return {
                "status": "verified_without_redispatch",
                **result,
                "physical_dispatch_attempted": False,
                "can_redispatch": False,
                "executor_available": True,
            }
        if existing.state == STATE_FAILED:
            raise StartDispatchError("start lifecycle is terminal failed")

        gate_payload = await async_bounded_dispatch_gate(
            hass,
            entry_id=entry_id,
            attempt_id=attempt_id,
            now=current,
        )
        lifecycle_value = gate_payload.get("lifecycle")
        lease_value = gate_payload.get("stop_lease")
        decision_value = gate_payload.get("bounded_dispatch_gate")
        if not isinstance(lifecycle_value, dict) or not isinstance(decision_value, dict):
            raise StartDispatchError("bounded dispatch gate audit evidence is invalid")
        try:
            gated_start = ExecutionLifecycleRecord.from_dict(lifecycle_value)
            decision = BoundedDispatchDecision(**decision_value)
            lease = ExecutionStopLease.from_dict(lease_value) if isinstance(lease_value, dict) else None
        except (TypeError, ValueError) as err:
            raise StartDispatchError("bounded dispatch gate audit evidence is invalid") from err

        if decision.status == BOUNDED_GATE_ALREADY_SATISFIED:
            result = await async_complete_already_satisfied_noop(
                hass,
                entry_id=entry_id,
                attempt_id=attempt_id,
                now=current,
            )
            return {
                "status": "already_satisfied_no_start",
                **result,
                "physical_dispatch_attempted": False,
                "executor_available": True,
            }
        if decision.status != BOUNDED_GATE_READY or not decision.can_start:
            raise StartDispatchError(
                f"bounded start is not ready: {decision.status}/{decision.reason}"
            )
        if lease is None:
            raise StartDispatchError("bounded start is missing its durable stop lease")
        if not _scheduler_ready(hass, entry_id):
            raise StartDispatchError("autonomous stop scheduler became unhealthy before start")
        try:
            await async_require_execution_armed(hass, entry_id)
        except ExecutionArmError as err:
            raise StartDispatchError(str(err)) from err

        current_start = await start_repository.async_get_by_attempt_id(attempt_id)
        if current_start != gated_start:
            raise StartDispatchError("start lifecycle changed during bounded gate evaluation")
        stop_repository = stop_lifecycle_repository(hass, entry_id)
        existing_stop = await stop_repository.async_get_by_start_lifecycle_id(
            current_start.lifecycle_id
        )
        if existing_stop is not None:
            raise StartDispatchError("prepared start unexpectedly already has stop ownership")

        # Persist unknown start outcome before any later physical boundary.
        dispatching = begin_dispatch(
            current_start,
            now=max(now_ts, current_start.updated_at),
        )
        persisted_dispatching = await start_repository.async_update(dispatching)

        try:
            stop_owned = ExecutionStopLifecycleRecord.owned(
                lease=lease,
                start_lifecycle=persisted_dispatching,
                created_at=max(now_ts, persisted_dispatching.updated_at),
            )
            stop_result = await stop_repository.async_create_owned(stop_owned)
            persisted_stop = stop_result.record
        except Exception as ownership_err:
            _, start_abort_err = await _abort_before_start_call(
                hass,
                entry_id=entry_id,
                start_dispatching=persisted_dispatching,
                stop_owned=None,
                reason="stop_ownership_persistence_failed_before_start",
                now_ts=now_ts,
            )
            detail = f"; start abort persistence failed: {start_abort_err}" if start_abort_err else ""
            raise StartDispatchError(
                f"durable stop ownership could not be persisted before start: {ownership_err}{detail}"
            ) from ownership_err

        # Make the newly durable stop obligation visible to the already-running
        # autonomous scheduler before crossing the start service boundary.
        await async_refresh_stop_scheduler_if_started(hass, entry_id)
        if not _scheduler_ready(hass, entry_id):
            stop_abort_err, start_abort_err = await _abort_before_start_call(
                hass,
                entry_id=entry_id,
                start_dispatching=persisted_dispatching,
                stop_owned=persisted_stop,
                reason="autonomous_stop_scheduler_unhealthy_before_start",
                now_ts=now_ts,
            )
            raise StartDispatchError(
                "autonomous stop scheduler became unhealthy before physical start"
                f"; stop abort error={stop_abort_err}; start abort error={start_abort_err}"
            )

        ownership = await async_start_stop_ownership_proof(
            hass,
            entry_id=entry_id,
            start=persisted_dispatching,
        )
        if not ownership.ownership_ready:
            stop_abort_err, start_abort_err = await _abort_before_start_call(
                hass,
                entry_id=entry_id,
                start_dispatching=persisted_dispatching,
                stop_owned=persisted_stop,
                reason=f"stop_ownership_proof_failed_before_start:{ownership.reason}",
                now_ts=now_ts,
            )
            raise StartDispatchError(
                f"durable stop ownership proof failed before physical start: {ownership.reason}"
                f"; stop abort error={stop_abort_err}; start abort error={start_abort_err}"
            )

        # ARM/DISARM changes and the actual service-call boundary share this lock.
        # Therefore, once DISARM returns, no later turn_on can cross the boundary.
        arm_error: ExecutionArmError | None = None
        call_error: Exception | None = None
        async with execution_arm_guard(hass, entry_id):
            try:
                await async_require_execution_armed(hass, entry_id)
            except ExecutionArmError as err:
                arm_error = err
            else:
                try:
                    await hass.services.async_call(
                        persisted_dispatching.service_domain,
                        persisted_dispatching.service_name,
                        {},
                        blocking=True,
                        context=context,
                        target={"entity_id": persisted_dispatching.entity_id},
                    )
                except Exception as err:
                    call_error = err

        if arm_error is not None:
            reason = (
                "execution_disarmed_before_start_call"
                if isinstance(arm_error, ExecutionDisarmedError)
                else "execution_arm_unavailable_before_start_call"
            )
            stop_abort_err, start_abort_err = await _abort_before_start_call(
                hass,
                entry_id=entry_id,
                start_dispatching=persisted_dispatching,
                stop_owned=persisted_stop,
                reason=reason,
                now_ts=now_ts,
            )
            raise StartDispatchError(
                f"physical start blocked by execution interlock: {arm_error}"
                f"; stop abort error={stop_abort_err}; start abort error={start_abort_err}"
            ) from arm_error

        if call_error is not None:
            recovery_err = await _persist_start_unknown_recovery(
                hass,
                entry_id=entry_id,
                record=persisted_dispatching,
                now_ts=now_ts,
            )
            await async_refresh_stop_scheduler_if_started(hass, entry_id)
            detail = f"; recovery persistence also failed: {recovery_err}" if recovery_err else ""
            raise StartDispatchUnknownOutcomeError(
                f"physical start call outcome is unknown: {call_error}{detail}"
            ) from call_error

        confirmed = confirm_dispatch(
            persisted_dispatching,
            now=max(now_ts, persisted_dispatching.updated_at),
        )
        try:
            persisted_confirmed = await start_repository.async_update(confirmed)
        except Exception as persist_err:
            recovery_err = await _persist_start_unknown_recovery(
                hass,
                entry_id=entry_id,
                record=persisted_dispatching,
                now_ts=now_ts,
            )
            await async_refresh_stop_scheduler_if_started(hass, entry_id)
            detail = f"; recovery persistence also failed: {recovery_err}" if recovery_err else ""
            raise StartDispatchUnknownOutcomeError(
                "physical start call returned normally, but confirmed evidence could not "
                f"be persisted: {persist_err}{detail}"
            ) from persist_err

        observed_state = _live_state(hass, persisted_confirmed.entity_id)
        normalized = observed_state.strip().lower() if isinstance(observed_state, str) else None
        if normalized == persisted_confirmed.desired_state:
            verified = verify_desired_state(
                persisted_confirmed,
                current_state=observed_state,
                now=max(now_ts, persisted_confirmed.updated_at),
            )
            try:
                persisted_verified = await start_repository.async_update(verified)
            except Exception as verify_persist_err:
                await async_refresh_stop_scheduler_if_started(hass, entry_id)
                return {
                    "status": "start_confirmed_verification_persistence_failed",
                    "lifecycle": persisted_confirmed.as_dict(),
                    "stop_lifecycle": persisted_stop.as_dict(),
                    "stop_ownership": ownership.as_dict(),
                    "current_state": normalized,
                    "verification_error": str(verify_persist_err),
                    "idempotent_replay": False,
                    "physical_dispatch_attempted": True,
                    "service_call_performed": True,
                    "execution_performed": True,
                    "state_transition_performed": True,
                    "can_redispatch": False,
                    "executor_available": True,
                }
            await async_refresh_stop_scheduler_if_started(hass, entry_id)
            return {
                "status": "start_verified",
                "lifecycle": persisted_verified.as_dict(),
                "stop_lifecycle": persisted_stop.as_dict(),
                "stop_ownership": ownership.as_dict(),
                "current_state": normalized,
                "idempotent_replay": False,
                "physical_dispatch_attempted": True,
                "service_call_performed": True,
                "execution_performed": True,
                "state_transition_performed": True,
                "can_redispatch": False,
                "executor_available": True,
            }

        await async_refresh_stop_scheduler_if_started(hass, entry_id)
        return {
            "status": "start_dispatched_pending_verification",
            "lifecycle": persisted_confirmed.as_dict(),
            "stop_lifecycle": persisted_stop.as_dict(),
            "stop_ownership": ownership.as_dict(),
            "current_state": normalized,
            "idempotent_replay": False,
            "physical_dispatch_attempted": True,
            "service_call_performed": True,
            "execution_performed": True,
            "state_transition_performed": True,
            "can_redispatch": False,
            "executor_available": True,
        }