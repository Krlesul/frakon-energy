"""Admin-only read-only execution readiness WebSocket API for FRAKON Energy."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from . import load_execution_consume_ws_api as consume_ws
from .const import DOMAIN
from .load_execution_action_snapshot_runtime import action_snapshot_repository
from .load_execution_policy import effective_policy_from_options
from .load_execution_readiness import (
    DEFAULT_START_GRACE_SECONDS,
    ExecutionReadinessError,
    evaluate_execution_readiness,
    load_plan_from_snapshot,
)
from .load_profiles import profile_by_id

COMMAND_EXECUTION_READINESS = f"{DOMAIN}/load_execution/readiness"
_REGISTERED_KEY = "load_execution_readiness_websocket_registered"


async def async_execution_readiness(
    hass: HomeAssistant,
    *,
    entry_id: str,
    attempt_id: str,
    plan_value: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the final read-only gate for one consumed execution attempt."""
    if not attempt_id:
        raise ExecutionReadinessError("attempt_id is required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ExecutionReadinessError("now must be timezone-aware")

    entry = consume_ws._entry(hass, entry_id)
    attempts = await consume_ws._attempt_repository(hass, entry_id).async_list()
    attempt = next((item for item in attempts if item.attempt_id == attempt_id), None)
    if attempt is None:
        raise ExecutionReadinessError(f"execution attempt not found: {attempt_id}")

    snapshot = await action_snapshot_repository(hass, entry_id).async_get_by_attempt_id(attempt_id)
    if snapshot is None:
        raise ExecutionReadinessError(
            f"immutable action snapshot not found for attempt: {attempt_id}"
        )

    try:
        profile = profile_by_id(entry.options, attempt.profile_id)
    except ValueError as err:
        raise ExecutionReadinessError(
            f"current load profile is unavailable: {attempt.profile_id}"
        ) from err
    policy = effective_policy_from_options(entry.options, attempt.profile_id)
    if not isinstance(plan_value, dict):
        raise ExecutionReadinessError("plan must be an object")
    plan = load_plan_from_snapshot(profile, plan_value)

    state = hass.states.get(snapshot.entity_id)
    current_state = str(state.state) if state is not None else None
    readiness = evaluate_execution_readiness(
        attempt=attempt,
        snapshot=snapshot,
        profile=profile,
        plan=plan,
        policy=policy,
        current_state=current_state,
        now=current,
        start_grace_seconds=DEFAULT_START_GRACE_SECONDS,
    )
    return {
        "entry_id": entry_id,
        "attempt": attempt.as_dict(),
        "action_snapshot": snapshot.as_dict(),
        "profile": profile.as_dict(),
        "policy": policy.as_dict(),
        "plan": plan.as_dict(),
        "readiness": readiness.as_dict(),
        "read_only": True,
        "execution_performed": False,
        "service_call_performed": False,
        "executor_available": False,
    }


@callback
def async_register_load_execution_readiness_websocket(hass: HomeAssistant) -> None:
    """Register the administrator-only read-only readiness command once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_EXECUTION_READINESS,
            vol.Required("entry_id"): str,
            vol.Required("attempt_id"): str,
            vol.Required("plan"): dict,
        }
    )
    async def websocket_readiness(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_execution_readiness(
                hass,
                entry_id=msg["entry_id"],
                attempt_id=msg["attempt_id"],
                plan_value=msg["plan"],
            )
        except (ExecutionReadinessError, consume_ws.ApprovalConsumeError, ValueError) as err:
            connection.send_error(msg["id"], "execution_readiness_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_readiness_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_readiness)
    domain_data[_REGISTERED_KEY] = True
