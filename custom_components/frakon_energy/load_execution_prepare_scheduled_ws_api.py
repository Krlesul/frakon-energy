"""Prepare a durable execution lifecycle from a persisted schedule only.

The client supplies no plan. The exact plan is loaded from the immutable durable
schedule and passed through the existing guarded lifecycle prepare transaction.
No dispatch command or Home Assistant service call exists here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_lifecycle import ExecutionLifecycleError
from .load_execution_lifecycle_recovery import LifecycleRecoveryBlockedError
from .load_execution_lifecycle_ws_api import (
    LifecyclePrepareError,
    async_prepare_execution_lifecycle,
)
from .load_execution_schedule import ExecutionScheduleError
from .load_execution_schedule_runtime import schedule_repository

COMMAND_PREPARE_SCHEDULED = f"{DOMAIN}/load_execution_lifecycle/prepare_scheduled"
_REGISTERED_KEY = "load_execution_prepare_scheduled_websocket_registered"


class PrepareScheduledError(ValueError):
    """Raised when a persisted schedule cannot be prepared safely."""


async def async_prepare_scheduled_execution(
    hass: HomeAssistant,
    *,
    entry_id: str,
    attempt_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Prepare lifecycle using only the exact durable schedule plan."""
    if not entry_id or not attempt_id:
        raise PrepareScheduledError("entry_id and attempt_id are required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise PrepareScheduledError("now must be timezone-aware")

    schedule = await schedule_repository(hass, entry_id).async_get_by_attempt_id(attempt_id)
    if schedule is None:
        raise PrepareScheduledError(f"execution schedule not found: {attempt_id}")
    schedule.validated()
    if schedule.entry_id != entry_id or schedule.attempt_id != attempt_id:
        raise PrepareScheduledError("persisted schedule scope does not match request")

    lifecycle = await async_prepare_execution_lifecycle(
        hass,
        entry_id=entry_id,
        attempt_id=attempt_id,
        plan_value=schedule.plan.as_dict(),
        now=current,
    )
    return {
        "entry_id": entry_id,
        "schedule": schedule.as_dict(),
        "lifecycle": lifecycle,
        "persisted_plan_used": True,
        "execution_performed": lifecycle.get("execution_performed") is True,
        "service_call_performed": lifecycle.get("service_call_performed"),
        "executor_available": False,
    }


@callback
def async_register_load_execution_prepare_scheduled_websocket(
    hass: HomeAssistant,
) -> None:
    """Register admin-only scheduled lifecycle preparation once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_PREPARE_SCHEDULED,
            vol.Required("entry_id"): str,
            vol.Required("attempt_id"): str,
        }
    )
    async def websocket_prepare_scheduled(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_prepare_scheduled_execution(
                hass,
                entry_id=msg["entry_id"],
                attempt_id=msg["attempt_id"],
            )
        except LifecycleRecoveryBlockedError as err:
            connection.send_error(msg["id"], "execution_lifecycle_recovery_blocked", str(err))
            return
        except (PrepareScheduledError, LifecyclePrepareError, ExecutionLifecycleError, ExecutionScheduleError, ValueError) as err:
            connection.send_error(msg["id"], "execution_prepare_scheduled_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_prepare_scheduled_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_prepare_scheduled)
    domain_data[_REGISTERED_KEY] = True
