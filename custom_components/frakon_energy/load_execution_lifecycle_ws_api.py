"""Admin-only lifecycle preparation/audit API for FRAKON Energy.

Only the inert ``prepared`` state can be created through this API. Dispatch and
service-call transitions are deliberately not exposed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .energy_load_planner import LoadPlan
from .load_execution_action_snapshot import ExecutionActionSnapshot
from .load_execution_attempt import ExecutionAttempt
from .load_execution_lifecycle import (
    ExecutionLifecycleConflictError,
    ExecutionLifecycleError,
    ExecutionLifecycleRecord,
    ExecutionPlanSnapshot,
)
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_execution_readiness import (
    ExecutionReadinessDecision,
    READINESS_READY,
)
from . import load_execution_readiness_ws_api as readiness_ws

COMMAND_PREPARE_LIFECYCLE = f"{DOMAIN}/load_execution_lifecycle/prepare"
COMMAND_LIST_LIFECYCLES = f"{DOMAIN}/load_execution_lifecycle/list"
_REGISTERED_KEY = "load_execution_lifecycle_websocket_registered"


class LifecyclePrepareError(ValueError):
    """Raised when an execution lifecycle cannot be safely prepared."""


def _plan_from_result(value: Any) -> LoadPlan:
    if not isinstance(value, dict):
        raise LifecyclePrepareError("readiness returned an invalid plan")
    try:
        return LoadPlan(
            load_id=str(value["load_id"]),
            name=str(value["name"]),
            starts_at=str(value["starts_at"]),
            ends_at=str(value["ends_at"]),
            duration_minutes=int(value["duration_minutes"]),
            interval_count=int(value["interval_count"]),
            power_kw=float(value["power_kw"]),
            average_czk_kwh=float(value["average_czk_kwh"]),
            minimum_czk_kwh=float(value["minimum_czk_kwh"]),
            maximum_czk_kwh=float(value["maximum_czk_kwh"]),
            estimated_energy_kwh=float(value["estimated_energy_kwh"]),
            estimated_cost_czk=float(value["estimated_cost_czk"]),
        )
    except (KeyError, TypeError, ValueError) as err:
        raise LifecyclePrepareError("readiness returned an invalid plan") from err


def _idempotent_plan_matches(existing: ExecutionLifecycleRecord, plan_value: Any) -> bool:
    if not isinstance(plan_value, dict):
        return False
    try:
        candidate = ExecutionPlanSnapshot.from_dict(plan_value)
    except ExecutionLifecycleError:
        return False
    return candidate == existing.plan and candidate.digest() == existing.plan_digest


async def async_prepare_execution_lifecycle(
    hass: HomeAssistant,
    *,
    entry_id: str,
    attempt_id: str,
    plan_value: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Re-run readiness and persist only an inert prepared lifecycle record."""
    if not entry_id or not attempt_id:
        raise LifecyclePrepareError("entry_id and attempt_id are required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise LifecyclePrepareError("now must be timezone-aware")

    repository = lifecycle_repository(hass, entry_id)
    existing = await repository.async_get_by_attempt_id(attempt_id)
    if existing is not None:
        if not _idempotent_plan_matches(existing, plan_value):
            raise ExecutionLifecycleConflictError(
                "execution attempt already has a lifecycle for a different plan snapshot"
            )
        return {
            "lifecycle": existing.as_dict(),
            "created": False,
            "idempotent_replay": True,
            "prepared_only": True,
            "execution_performed": False,
            "service_call_performed": existing.as_dict()["service_call_performed"],
            "executor_available": False,
        }

    readiness_payload = await readiness_ws.async_execution_readiness(
        hass,
        entry_id=entry_id,
        attempt_id=attempt_id,
        plan_value=plan_value,
        now=current,
    )
    readiness_value = readiness_payload.get("readiness")
    if not isinstance(readiness_value, dict):
        raise LifecyclePrepareError("readiness response is invalid")
    try:
        readiness = ExecutionReadinessDecision(**readiness_value)
    except (TypeError, ValueError) as err:
        raise LifecyclePrepareError("readiness response is invalid") from err
    if readiness.status != READINESS_READY or not readiness.action_required:
        raise LifecyclePrepareError(
            f"execution is not ready for preparation: {readiness.status}/{readiness.reason}"
        )

    attempt_value = readiness_payload.get("attempt")
    snapshot_value = readiness_payload.get("action_snapshot")
    if not isinstance(attempt_value, dict) or not isinstance(snapshot_value, dict):
        raise LifecyclePrepareError("readiness audit records are invalid")
    attempt = ExecutionAttempt.from_dict(attempt_value)
    action_snapshot = ExecutionActionSnapshot.from_dict(snapshot_value)
    plan = _plan_from_result(readiness_payload.get("plan"))

    record = ExecutionLifecycleRecord.prepared(
        attempt=attempt,
        action_snapshot=action_snapshot,
        plan=plan,
        readiness=readiness,
        created_at=int(current.timestamp()),
    )
    result = await repository.async_prepare(record)
    return {
        **result.as_dict(),
        "prepared_only": True,
        "execution_performed": False,
        "service_call_performed": False,
        "executor_available": False,
    }


async def async_list_execution_lifecycles(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> dict[str, Any]:
    if not entry_id:
        raise LifecyclePrepareError("entry_id is required")
    records = await lifecycle_repository(hass, entry_id).async_list()
    return {
        "entry_id": entry_id,
        "lifecycles": [record.as_dict() for record in records],
        "read_only": True,
        "execution_performed": False,
        "executor_available": False,
    }


@callback
def async_register_load_execution_lifecycle_websocket(hass: HomeAssistant) -> None:
    """Register prepare + read-only audit. No dispatch command is registered."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_PREPARE_LIFECYCLE,
            vol.Required("entry_id"): str,
            vol.Required("attempt_id"): str,
            vol.Required("plan"): dict,
        }
    )
    async def websocket_prepare(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_prepare_execution_lifecycle(
                hass,
                entry_id=msg["entry_id"],
                attempt_id=msg["attempt_id"],
                plan_value=msg["plan"],
            )
        except ExecutionLifecycleConflictError as err:
            connection.send_error(msg["id"], "execution_lifecycle_conflict", str(err))
            return
        except (LifecyclePrepareError, ExecutionLifecycleError, ValueError) as err:
            connection.send_error(msg["id"], "execution_lifecycle_prepare_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_lifecycle_prepare_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_LIST_LIFECYCLES,
            vol.Required("entry_id"): str,
        }
    )
    async def websocket_list(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_list_execution_lifecycles(hass, entry_id=msg["entry_id"])
        except (LifecyclePrepareError, ExecutionLifecycleError, ValueError) as err:
            connection.send_error(msg["id"], "execution_lifecycle_list_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_lifecycle_list_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_prepare)
    websocket_api.async_register_command(hass, websocket_list)
    domain_data[_REGISTERED_KEY] = True
