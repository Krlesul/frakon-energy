"""Administrator-only read-only stop deadline diagnostics."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_stop_due_gate import evaluate_stop_due_gate
from .load_execution_stop_lifecycle_runtime import stop_lifecycle_repository
from .load_execution_stop_recovery import (
    STOP_RECOVERY_OK,
    stop_recovery_summary,
)

COMMAND_STOP_DUE = f"{DOMAIN}/load_execution_stop/due"
_REGISTERED_KEY = "load_execution_stop_due_websocket_registered"


async def async_stop_due_diagnostics(
    hass: HomeAssistant,
    *,
    entry_id: str,
    start_lifecycle_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return live deadline decisions for persisted stop lifecycles without mutation."""
    if not entry_id:
        raise ValueError("entry_id is required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    recovery = stop_recovery_summary(hass, entry_id)
    recovery_ready = recovery.status == STOP_RECOVERY_OK
    records = await stop_lifecycle_repository(hass, entry_id).async_list()
    if start_lifecycle_id is not None:
        records = tuple(
            record for record in records
            if record.start_lifecycle_id == start_lifecycle_id
        )
        if not records:
            raise ValueError(f"stop lifecycle not found: {start_lifecycle_id}")

    items: list[dict[str, Any]] = []
    stop_candidates: list[str] = []
    noop_candidates: list[str] = []
    verify_candidates: list[str] = []
    for record in records:
        state = hass.states.get(record.entity_id)
        current_state = str(state.state) if state is not None else None
        decision = evaluate_stop_due_gate(
            record=record,
            current_state=current_state,
            now=current,
            recovery_ready=recovery_ready,
        )
        items.append(
            {
                "stop_lifecycle": record.as_dict(),
                "decision": decision.as_dict(),
            }
        )
        if decision.can_dispatch_stop:
            stop_candidates.append(record.start_lifecycle_id)
        if decision.can_complete_noop:
            noop_candidates.append(record.start_lifecycle_id)
        if decision.can_mark_verified:
            verify_candidates.append(record.start_lifecycle_id)

    return {
        "entry_id": entry_id,
        "recovery": recovery.as_dict(),
        "items": items,
        "stop_candidates": stop_candidates,
        "noop_candidates": noop_candidates,
        "verify_candidates": verify_candidates,
        "read_only": True,
        "state_transition_performed": False,
        "service_call_performed": False,
        "execution_performed": False,
        "executor_available": False,
    }


@callback
def async_register_load_execution_stop_due_websocket(hass: HomeAssistant) -> None:
    """Register read-only stop due diagnostics once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    schema = {
        vol.Required("type"): COMMAND_STOP_DUE,
        vol.Required("entry_id"): str,
        vol.Optional("start_lifecycle_id"): str,
    }

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(schema)
    async def websocket_stop_due(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_stop_due_diagnostics(
                hass,
                entry_id=msg["entry_id"],
                start_lifecycle_id=msg.get("start_lifecycle_id"),
            )
        except ValueError as err:
            connection.send_error(msg["id"], "stop_due_invalid", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "stop_due_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_stop_due)
    domain_data[_REGISTERED_KEY] = True
