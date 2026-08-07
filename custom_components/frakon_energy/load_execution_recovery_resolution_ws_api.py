"""Administrator-only read-only recovery resolution planning for FRAKON Energy."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_lifecycle_recovery import (
    LifecycleRecoveryBlockedError,
    assert_lifecycle_recovery_ready,
    lifecycle_recovery_summary,
)
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_execution_recovery_resolution import evaluate_recovery_resolution
from .load_execution_start_stop_ownership import async_start_stop_ownership_proof

COMMAND_RECOVERY_RESOLUTION = f"{DOMAIN}/load_execution_lifecycle/recovery_resolution"
_REGISTERED_KEY = "load_execution_recovery_resolution_websocket_registered"


class RecoveryResolutionLookupError(ValueError):
    """Raised when a durable lifecycle cannot be resolved for planning."""


def _live_state(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    return str(state.state) if state is not None else None


async def async_recovery_resolution_plan(
    hass: HomeAssistant,
    *,
    entry_id: str,
    attempt_id: str,
) -> dict[str, Any]:
    """Return a read-only recovery resolution plan for one lifecycle attempt."""
    if not entry_id or not attempt_id:
        raise RecoveryResolutionLookupError("entry_id and attempt_id are required")

    assert_lifecycle_recovery_ready(hass, entry_id)
    repository = lifecycle_repository(hass, entry_id)
    record = await repository.async_get_by_attempt_id(attempt_id)
    if record is None:
        raise RecoveryResolutionLookupError(
            f"execution lifecycle not found: {attempt_id}"
        )

    ownership = await async_start_stop_ownership_proof(
        hass,
        entry_id=entry_id,
        start=record,
    )
    decision = evaluate_recovery_resolution(
        record,
        current_state=_live_state(hass, record.entity_id),
        stop_ownership_ready=ownership.ownership_ready,
    )
    return {
        "entry_id": entry_id,
        "recovery": lifecycle_recovery_summary(hass, entry_id).as_dict(),
        "lifecycle": record.as_dict(),
        "stop_ownership": ownership.as_dict(),
        "resolution": decision.as_dict(),
        "read_only": True,
        "resolution_performed": False,
        "state_transition_performed": False,
        "service_call_performed": False,
        "execution_performed": False,
        "executor_available": False,
    }


@callback
def async_register_load_execution_recovery_resolution_websocket(
    hass: HomeAssistant,
) -> None:
    """Register read-only recovery resolution planning once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_RECOVERY_RESOLUTION,
            vol.Required("entry_id"): str,
            vol.Required("attempt_id"): str,
        }
    )
    async def websocket_recovery_resolution(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_recovery_resolution_plan(
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
        except RecoveryResolutionLookupError as err:
            connection.send_error(
                msg["id"],
                "execution_lifecycle_recovery_resolution_invalid",
                str(err),
            )
            return
        except Exception as err:
            connection.send_error(
                msg["id"],
                "execution_lifecycle_recovery_resolution_unavailable",
                str(err),
            )
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_recovery_resolution)
    domain_data[_REGISTERED_KEY] = True
