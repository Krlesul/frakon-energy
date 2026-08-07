"""Administrator-only final read-only dispatch gate for FRAKON Energy."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from . import load_execution_consume_ws_api as consume_ws
from .const import DOMAIN
from .load_execution_action_snapshot_runtime import action_snapshot_repository
from .load_execution_dispatch_gate import evaluate_dispatch_gate
from .load_execution_lifecycle_recovery import (
    LifecycleRecoveryBlockedError,
    assert_lifecycle_recovery_ready,
)
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_execution_policy import effective_policy_from_options
from .load_execution_readiness import (
    DEFAULT_START_GRACE_SECONDS,
    ExecutionReadinessError,
    evaluate_execution_readiness,
)
from .load_profiles import profile_by_id

COMMAND_DISPATCH_GATE = f"{DOMAIN}/load_execution_lifecycle/dispatch_gate"
_REGISTERED_KEY = "load_execution_dispatch_gate_websocket_registered"


class DispatchGateLookupError(ValueError):
    """Raised when durable dispatch evidence cannot be resolved."""


def _live_state(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    return str(state.state) if state is not None else None


async def async_execution_dispatch_gate(
    hass: HomeAssistant,
    *,
    entry_id: str,
    attempt_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Revalidate one durable prepared lifecycle without dispatching anything."""
    if not entry_id or not attempt_id:
        raise DispatchGateLookupError("entry_id and attempt_id are required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise DispatchGateLookupError("now must be timezone-aware")

    assert_lifecycle_recovery_ready(hass, entry_id)
    entry = consume_ws._entry(hass, entry_id)

    lifecycle = await lifecycle_repository(hass, entry_id).async_get_by_attempt_id(attempt_id)
    if lifecycle is None:
        raise DispatchGateLookupError(
            f"execution lifecycle not found: {attempt_id}"
        )

    attempts = await consume_ws._attempt_repository(hass, entry_id).async_list()
    attempt = next((item for item in attempts if item.attempt_id == attempt_id), None)
    if attempt is None:
        raise DispatchGateLookupError(
            f"execution attempt not found: {attempt_id}"
        )

    snapshot = await action_snapshot_repository(hass, entry_id).async_get_by_attempt_id(
        attempt_id
    )
    if snapshot is None:
        raise DispatchGateLookupError(
            f"immutable action snapshot not found for attempt: {attempt_id}"
        )

    try:
        profile = profile_by_id(entry.options, attempt.profile_id)
    except ValueError as err:
        raise DispatchGateLookupError(
            f"current load profile is unavailable: {attempt.profile_id}"
        ) from err
    policy = effective_policy_from_options(entry.options, attempt.profile_id)
    plan = lifecycle.plan.to_load_plan()
    current_state = _live_state(hass, lifecycle.entity_id)

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
    gate = evaluate_dispatch_gate(
        lifecycle=lifecycle,
        attempt=attempt,
        snapshot=snapshot,
        readiness=readiness,
    )
    return {
        "entry_id": entry_id,
        "lifecycle": lifecycle.as_dict(),
        "attempt": attempt.as_dict(),
        "action_snapshot": snapshot.as_dict(),
        "profile": profile.as_dict(),
        "policy": policy.as_dict(),
        "plan": plan.as_dict(),
        "readiness": readiness.as_dict(),
        "dispatch_gate": gate.as_dict(),
        "read_only": True,
        "state_transition_performed": False,
        "service_call_performed": False,
        "execution_performed": False,
        "executor_available": False,
    }


@callback
def async_register_load_execution_dispatch_gate_websocket(hass: HomeAssistant) -> None:
    """Register the administrator-only read-only dispatch gate once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_DISPATCH_GATE,
            vol.Required("entry_id"): str,
            vol.Required("attempt_id"): str,
        }
    )
    async def websocket_dispatch_gate(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_execution_dispatch_gate(
                hass,
                entry_id=msg["entry_id"],
                attempt_id=msg["attempt_id"],
            )
        except LifecycleRecoveryBlockedError as err:
            connection.send_error(
                msg["id"],
                "execution_lifecycle_recovery_blocked",
                str(err),
            )
            return
        except (
            DispatchGateLookupError,
            ExecutionReadinessError,
            consume_ws.ApprovalConsumeError,
            ValueError,
        ) as err:
            connection.send_error(
                msg["id"],
                "execution_dispatch_gate_rejected",
                str(err),
            )
            return
        except Exception as err:
            connection.send_error(
                msg["id"],
                "execution_dispatch_gate_unavailable",
                str(err),
            )
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_dispatch_gate)
    domain_data[_REGISTERED_KEY] = True
