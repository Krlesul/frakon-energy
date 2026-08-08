"""Administrator-only durable pending-run preparation, cancellation and audit API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_action_snapshot import ExecutionActionSnapshot
from .load_execution_attempt import ExecutionAttempt
from .load_execution_lifecycle import ExecutionPlanSnapshot
from .load_execution_lifecycle_recovery import (
    LifecycleRecoveryBlockedError,
    assert_lifecycle_recovery_ready,
)
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_execution_pending_run import (
    ExecutionPendingRun,
    PendingRunConflictError,
    PendingRunError,
)
from .load_execution_pending_run_cancellation import (
    PendingRunCancellationConflictError,
    PendingRunCancellationError,
    async_cancel_pending_run_before_lifecycle,
    cancellation_repository,
)
from .load_execution_pending_run_retention_runtime import async_run_pending_run_retention_best_effort
from .load_execution_pending_run_runtime import pending_run_repository
from .load_execution_readiness import (
    ExecutionReadinessDecision,
    READINESS_READY,
    READINESS_WAITING,
)
from .load_execution_readiness_ws_api import async_execution_readiness

COMMAND_CREATE_PENDING_RUN = f"{DOMAIN}/load_execution_pending_run/create"
COMMAND_CANCEL_PENDING_RUN = f"{DOMAIN}/load_execution_pending_run/cancel"
COMMAND_LIST_PENDING_RUNS = f"{DOMAIN}/load_execution_pending_run/list"
PENDING_RUN_CANCEL_CONFIRMATION = "CANCEL"
_REGISTERED_KEY = "load_execution_pending_run_websocket_registered"


class PendingRunPrepareError(ValueError):
    """Raised when a pending run cannot be safely persisted."""


def _candidate_plan(value: Any) -> ExecutionPlanSnapshot:
    if not isinstance(value, dict):
        raise PendingRunPrepareError("plan must be an object")
    try:
        return ExecutionPlanSnapshot.from_dict(value)
    except ValueError as err:
        raise PendingRunPrepareError("plan snapshot is invalid") from err


async def async_create_pending_run(
    hass: HomeAssistant,
    *,
    entry_id: str,
    attempt_id: str,
    plan_value: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist an exact approved future run without preparing or dispatching it."""
    if not entry_id or not attempt_id:
        raise PendingRunPrepareError("entry_id and attempt_id are required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise PendingRunPrepareError("now must be timezone-aware")
    candidate_plan = _candidate_plan(plan_value)
    assert_lifecycle_recovery_ready(hass, entry_id)

    cancellation = await cancellation_repository(hass, entry_id).async_get_by_attempt_id(
        attempt_id
    )
    if cancellation is not None:
        raise PendingRunPrepareError(
            "execution attempt was durably cancelled and cannot be rescheduled; "
            "create a new approval/attempt instead"
        )

    repository = pending_run_repository(hass, entry_id)
    existing = await repository.async_get_by_attempt_id(attempt_id)
    if existing is not None:
        if existing.plan != candidate_plan or existing.plan_digest != candidate_plan.digest():
            raise PendingRunConflictError(
                "execution attempt already has a pending run for a different plan snapshot"
            )
        payload = {
            "pending_run": existing.as_dict(),
            "created": False,
            "idempotent_replay": True,
            "scheduled_only": True,
            "service_call_performed": False,
            "execution_performed": False,
            "executor_available": False,
        }
        from .load_execution_pending_run_scheduler import (  # noqa: PLC0415
            async_refresh_pending_run_scheduler_if_started,
        )

        await async_refresh_pending_run_scheduler_if_started(hass, entry_id)
        return payload

    lifecycle = await lifecycle_repository(hass, entry_id).async_get_by_attempt_id(attempt_id)
    if lifecycle is not None:
        raise PendingRunPrepareError(
            "execution attempt already has a durable lifecycle and cannot be newly scheduled"
        )

    readiness_payload = await async_execution_readiness(
        hass,
        entry_id=entry_id,
        attempt_id=attempt_id,
        plan_value=plan_value,
        now=current,
    )
    readiness_value = readiness_payload.get("readiness")
    attempt_value = readiness_payload.get("attempt")
    snapshot_value = readiness_payload.get("action_snapshot")
    exact_plan_value = readiness_payload.get("plan")
    if not isinstance(readiness_value, dict):
        raise PendingRunPrepareError("readiness response is invalid")
    if not isinstance(attempt_value, dict) or not isinstance(snapshot_value, dict):
        raise PendingRunPrepareError("readiness audit records are invalid")
    if not isinstance(exact_plan_value, dict):
        raise PendingRunPrepareError("readiness exact plan is invalid")
    try:
        readiness = ExecutionReadinessDecision(**readiness_value)
        attempt = ExecutionAttempt.from_dict(attempt_value)
        action_snapshot = ExecutionActionSnapshot.from_dict(snapshot_value)
        exact_plan = ExecutionPlanSnapshot.from_dict(exact_plan_value)
    except (TypeError, ValueError) as err:
        raise PendingRunPrepareError("readiness audit evidence is invalid") from err

    if readiness.status not in (READINESS_WAITING, READINESS_READY):
        raise PendingRunPrepareError(
            f"execution is not schedulable: {readiness.status}/{readiness.reason}"
        )
    if readiness.attempt_id != attempt_id or attempt.attempt_id != attempt_id:
        raise PendingRunPrepareError("readiness attempt identity changed")
    if exact_plan != candidate_plan or exact_plan.digest() != candidate_plan.digest():
        raise PendingRunPrepareError("readiness plan does not match requested exact plan")

    record = ExecutionPendingRun.from_records(
        attempt=attempt,
        action_snapshot=action_snapshot,
        plan=exact_plan,
        created_at=int(current.timestamp()),
    )
    result = await repository.async_record(record)

    # Housekeeping cannot make a successful scheduling mutation fail. The new
    # record is necessarily young, so retention can only remove unrelated old
    # redundant pending-run copies.
    await async_run_pending_run_retention_best_effort(
        hass,
        entry_id=entry_id,
        now=current,
    )

    from .load_execution_pending_run_scheduler import (  # noqa: PLC0415
        async_refresh_pending_run_scheduler_if_started,
    )

    await async_refresh_pending_run_scheduler_if_started(hass, entry_id)
    return {
        **result.as_dict(),
        "scheduled_only": True,
        "readiness_at_schedule": readiness.as_dict(),
    }


async def async_cancel_pending_run(
    hass: HomeAssistant,
    *,
    entry_id: str,
    attempt_id: str,
    cancelled_by: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Durably revoke one pending run only before any lifecycle exists."""
    result = await async_cancel_pending_run_before_lifecycle(
        hass,
        entry_id=entry_id,
        attempt_id=attempt_id,
        cancelled_by=cancelled_by,
        now=now,
    )

    refresh_error: str | None = None
    try:
        from .load_execution_pending_run_scheduler import (  # noqa: PLC0415
            async_refresh_pending_run_scheduler_if_started,
        )

        await async_refresh_pending_run_scheduler_if_started(hass, entry_id)
    except Exception as err:
        # The durable tombstone is already authoritative. A runtime refresh
        # failure must not make cancellation appear rolled back.
        refresh_error = str(err)

    return {
        **result.as_dict(),
        "cancelled": True,
        "terminal_for_attempt": True,
        "new_lifecycle_allowed": False,
        "runtime_refresh_error": refresh_error,
        "service_call_performed": False,
        "execution_performed": False,
        "executor_available": False,
    }


async def async_list_pending_runs(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> dict[str, Any]:
    if not entry_id:
        raise PendingRunPrepareError("entry_id is required")
    records = await pending_run_repository(hass, entry_id).async_list()
    cancellations = await cancellation_repository(hass, entry_id).async_list()
    return {
        "entry_id": entry_id,
        "pending_runs": [record.as_dict() for record in records],
        "cancellations": [record.as_dict() for record in cancellations],
        "read_only": True,
        "service_call_performed": False,
        "execution_performed": False,
        "executor_available": False,
    }


def _actor(connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> str | None:
    return connection.context(msg).user_id


@callback
def async_register_load_execution_pending_run_websocket(hass: HomeAssistant) -> None:
    """Register pending-run create/cancel/list commands once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_CREATE_PENDING_RUN,
            vol.Required("entry_id"): str,
            vol.Required("attempt_id"): str,
            vol.Required("plan"): dict,
        }
    )
    async def websocket_create_pending_run(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_create_pending_run(
                hass,
                entry_id=msg["entry_id"],
                attempt_id=msg["attempt_id"],
                plan_value=msg["plan"],
            )
        except LifecycleRecoveryBlockedError as err:
            connection.send_error(
                msg["id"],
                "execution_lifecycle_recovery_blocked",
                str(err),
            )
            return
        except PendingRunConflictError as err:
            connection.send_error(msg["id"], "execution_pending_run_conflict", str(err))
            return
        except (PendingRunPrepareError, PendingRunError, ValueError) as err:
            connection.send_error(msg["id"], "execution_pending_run_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_pending_run_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_CANCEL_PENDING_RUN,
            vol.Required("entry_id"): str,
            vol.Required("attempt_id"): str,
            vol.Required("confirmation"): vol.In((PENDING_RUN_CANCEL_CONFIRMATION,)),
        }
    )
    async def websocket_cancel_pending_run(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_cancel_pending_run(
                hass,
                entry_id=msg["entry_id"],
                attempt_id=msg["attempt_id"],
                cancelled_by=_actor(connection, msg),
            )
        except PendingRunCancellationConflictError as err:
            connection.send_error(
                msg["id"],
                "execution_pending_run_cancel_conflict",
                str(err),
            )
            return
        except (PendingRunCancellationError, PendingRunError, ValueError) as err:
            connection.send_error(
                msg["id"],
                "execution_pending_run_cancel_rejected",
                str(err),
            )
            return
        except Exception as err:
            connection.send_error(
                msg["id"],
                "execution_pending_run_cancel_unavailable",
                str(err),
            )
            return
        connection.send_result(msg["id"], result)

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_LIST_PENDING_RUNS,
            vol.Required("entry_id"): str,
        }
    )
    async def websocket_list_pending_runs(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_list_pending_runs(hass, entry_id=msg["entry_id"])
        except (PendingRunPrepareError, PendingRunError, ValueError) as err:
            connection.send_error(msg["id"], "execution_pending_run_list_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_pending_run_list_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_create_pending_run)
    websocket_api.async_register_command(hass, websocket_cancel_pending_run)
    websocket_api.async_register_command(hass, websocket_list_pending_runs)
    domain_data[_REGISTERED_KEY] = True
