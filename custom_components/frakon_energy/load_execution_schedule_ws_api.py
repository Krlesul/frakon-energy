"""Administrator-only durable pre-start scheduling API for FRAKON Energy.

Schedule creation persists an already approved exact plan while readiness is
``waiting`` or ``ready``. Later readiness checks use only that persisted plan.
No executor or Home Assistant service call exists here.
"""

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
from .load_execution_readiness import ExecutionReadinessDecision
from .load_execution_schedule import (
    ExecutionSchedule,
    ExecutionScheduleConflictError,
    ExecutionScheduleError,
)
from .load_execution_schedule_runtime import schedule_repository
from . import load_execution_readiness_ws_api as readiness_ws

COMMAND_CREATE_SCHEDULE = f"{DOMAIN}/load_execution_schedule/create"
COMMAND_LIST_SCHEDULES = f"{DOMAIN}/load_execution_schedule/list"
COMMAND_SCHEDULE_READINESS = f"{DOMAIN}/load_execution_schedule/readiness"
_REGISTERED_KEY = "load_execution_schedule_websocket_registered"


class ExecutionScheduleApiError(ValueError):
    """Raised when a schedule API request cannot be completed safely."""


def _candidate_plan(value: Any) -> ExecutionPlanSnapshot:
    if not isinstance(value, dict):
        raise ExecutionScheduleApiError("plan must be an object")
    try:
        return ExecutionPlanSnapshot.from_dict(value)
    except ValueError as err:
        raise ExecutionScheduleApiError(f"invalid plan snapshot: {err}") from err


def _existing_plan_matches(schedule: ExecutionSchedule, plan_value: Any) -> bool:
    try:
        candidate = _candidate_plan(plan_value)
    except ExecutionScheduleApiError:
        return False
    return candidate == schedule.plan and candidate.digest() == schedule.plan_digest


async def async_create_execution_schedule(
    hass: HomeAssistant,
    *,
    entry_id: str,
    attempt_id: str,
    plan_value: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist an exact approved plan before the final lifecycle prepare window."""
    if not entry_id or not attempt_id:
        raise ExecutionScheduleApiError("entry_id and attempt_id are required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ExecutionScheduleApiError("now must be timezone-aware")
    assert_lifecycle_recovery_ready(hass, entry_id)

    repository = schedule_repository(hass, entry_id)
    existing = await repository.async_get_by_attempt_id(attempt_id)
    if existing is not None:
        if not _existing_plan_matches(existing, plan_value):
            raise ExecutionScheduleConflictError(
                "execution attempt already has a schedule for a different plan snapshot"
            )
        return {
            "schedule": existing.as_dict(),
            "created": False,
            "idempotent_replay": True,
            "execution_performed": False,
            "service_call_performed": False,
            "executor_available": False,
        }

    readiness_payload = await readiness_ws.async_execution_readiness(
        hass,
        entry_id=entry_id,
        attempt_id=attempt_id,
        plan_value=plan_value,
        now=current,
    )
    attempt_value = readiness_payload.get("attempt")
    snapshot_value = readiness_payload.get("action_snapshot")
    readiness_value = readiness_payload.get("readiness")
    plan_result = readiness_payload.get("plan")
    if not isinstance(attempt_value, dict) or not isinstance(snapshot_value, dict):
        raise ExecutionScheduleApiError("readiness returned invalid audit records")
    if not isinstance(readiness_value, dict) or not isinstance(plan_result, dict):
        raise ExecutionScheduleApiError("readiness returned invalid plan/readiness data")
    try:
        attempt = ExecutionAttempt.from_dict(attempt_value)
        action_snapshot = ExecutionActionSnapshot.from_dict(snapshot_value)
        readiness = ExecutionReadinessDecision(**readiness_value)
        plan = ExecutionPlanSnapshot.from_dict(plan_result).to_load_plan()
    except (TypeError, ValueError) as err:
        raise ExecutionScheduleApiError(f"readiness payload is invalid: {err}") from err

    schedule = ExecutionSchedule.from_approved_readiness(
        attempt=attempt,
        action_snapshot=action_snapshot,
        plan=plan,
        readiness=readiness,
        created_at=int(current.timestamp()),
    )
    result = await repository.async_record(schedule)
    return result.as_dict()


async def async_list_execution_schedules(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> dict[str, Any]:
    """Return immutable durable schedules without changing execution state."""
    if not entry_id:
        raise ExecutionScheduleApiError("entry_id is required")
    schedules = await schedule_repository(hass, entry_id).async_list()
    return {
        "entry_id": entry_id,
        "schedules": [schedule.as_dict() for schedule in schedules],
        "read_only": True,
        "execution_performed": False,
        "service_call_performed": False,
        "executor_available": False,
    }


async def async_execution_schedule_readiness(
    hass: HomeAssistant,
    *,
    entry_id: str,
    attempt_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Re-evaluate current readiness using only the persisted immutable plan."""
    if not entry_id or not attempt_id:
        raise ExecutionScheduleApiError("entry_id and attempt_id are required")
    schedule = await schedule_repository(hass, entry_id).async_get_by_attempt_id(attempt_id)
    if schedule is None:
        raise ExecutionScheduleApiError(f"execution schedule not found: {attempt_id}")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ExecutionScheduleApiError("now must be timezone-aware")

    readiness = await readiness_ws.async_execution_readiness(
        hass,
        entry_id=entry_id,
        attempt_id=attempt_id,
        plan_value=schedule.plan.as_dict(),
        now=current,
    )
    return {
        "entry_id": entry_id,
        "schedule": schedule.as_dict(),
        "readiness": readiness,
        "read_only": True,
        "persisted_plan_used": True,
        "execution_performed": False,
        "service_call_performed": False,
        "executor_available": False,
    }


@callback
def async_register_load_execution_schedule_websocket(hass: HomeAssistant) -> None:
    """Register schedule create/list/readiness commands once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_CREATE_SCHEDULE,
            vol.Required("entry_id"): str,
            vol.Required("attempt_id"): str,
            vol.Required("plan"): dict,
        }
    )
    async def websocket_create(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_create_execution_schedule(
                hass,
                entry_id=msg["entry_id"],
                attempt_id=msg["attempt_id"],
                plan_value=msg["plan"],
            )
        except LifecycleRecoveryBlockedError as err:
            connection.send_error(msg["id"], "execution_schedule_recovery_blocked", str(err))
            return
        except ExecutionScheduleConflictError as err:
            connection.send_error(msg["id"], "execution_schedule_conflict", str(err))
            return
        except (ExecutionScheduleApiError, ExecutionScheduleError, ValueError) as err:
            connection.send_error(msg["id"], "execution_schedule_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_schedule_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_LIST_SCHEDULES,
            vol.Required("entry_id"): str,
        }
    )
    async def websocket_list(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_list_execution_schedules(hass, entry_id=msg["entry_id"])
        except (ExecutionScheduleApiError, ExecutionScheduleError, ValueError) as err:
            connection.send_error(msg["id"], "execution_schedule_list_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_schedule_list_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_SCHEDULE_READINESS,
            vol.Required("entry_id"): str,
            vol.Required("attempt_id"): str,
        }
    )
    async def websocket_readiness(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_execution_schedule_readiness(
                hass,
                entry_id=msg["entry_id"],
                attempt_id=msg["attempt_id"],
            )
        except (ExecutionScheduleApiError, ExecutionScheduleError, ValueError) as err:
            connection.send_error(msg["id"], "execution_schedule_readiness_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_schedule_readiness_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_create)
    websocket_api.async_register_command(hass, websocket_list)
    websocket_api.async_register_command(hass, websocket_readiness)
    domain_data[_REGISTERED_KEY] = True
