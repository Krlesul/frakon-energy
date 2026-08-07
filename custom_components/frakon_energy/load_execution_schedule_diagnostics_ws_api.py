"""Administrator-only read-only schedule timing diagnostics for FRAKON Energy."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_lifecycle_recovery import RECOVERY_OK, lifecycle_recovery_summary
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_execution_schedule_diagnostics import (
    ScheduleDiagnosticError,
    evaluate_schedule_timing,
)
from .load_execution_schedule_runtime import schedule_repository

COMMAND_SCHEDULE_DIAGNOSTICS = f"{DOMAIN}/load_execution_schedule/diagnostics"
_REGISTERED_KEY = "load_execution_schedule_diagnostics_websocket_registered"


async def async_execution_schedule_diagnostics(
    hass: HomeAssistant,
    *,
    entry_id: str,
    attempt_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return scheduler timing diagnostics without mutating any execution state."""
    if not entry_id:
        raise ScheduleDiagnosticError("entry_id is required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ScheduleDiagnosticError("now must be timezone-aware")

    schedules = await schedule_repository(hass, entry_id).async_list()
    if attempt_id is not None:
        schedules = tuple(item for item in schedules if item.attempt_id == attempt_id)
        if not schedules:
            raise ScheduleDiagnosticError(f"execution schedule not found: {attempt_id}")

    lifecycles = await lifecycle_repository(hass, entry_id).async_list()
    lifecycle_by_attempt = {item.attempt_id: item for item in lifecycles}
    recovery = lifecycle_recovery_summary(hass, entry_id)
    recovery_ready = recovery.status == RECOVERY_OK

    diagnostics = [
        evaluate_schedule_timing(
            schedule,
            lifecycle=lifecycle_by_attempt.get(schedule.attempt_id),
            now=current,
            recovery_ready=recovery_ready,
        ).as_dict()
        for schedule in schedules
    ]
    return {
        "entry_id": entry_id,
        "recovery": recovery.as_dict(),
        "diagnostics": diagnostics,
        "scheduler_prepare_candidates": [
            item["attempt_id"] for item in diagnostics if item["scheduler_should_prepare"]
        ],
        "read_only": True,
        "state_transition_performed": False,
        "execution_performed": False,
        "service_call_performed": False,
        "executor_available": False,
    }


@callback
def async_register_load_execution_schedule_diagnostics_websocket(
    hass: HomeAssistant,
) -> None:
    """Register administrator-only read-only schedule diagnostics once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_SCHEDULE_DIAGNOSTICS,
            vol.Required("entry_id"): str,
            vol.Optional("attempt_id"): str,
        }
    )
    async def websocket_diagnostics(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_execution_schedule_diagnostics(
                hass,
                entry_id=msg["entry_id"],
                attempt_id=msg.get("attempt_id"),
            )
        except (ScheduleDiagnosticError, ValueError) as err:
            connection.send_error(msg["id"], "execution_schedule_diagnostics_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_schedule_diagnostics_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_diagnostics)
    domain_data[_REGISTERED_KEY] = True
