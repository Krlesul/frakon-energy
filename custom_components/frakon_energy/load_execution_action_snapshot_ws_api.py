"""Admin-only read-only audit/revalidation API for execution action snapshots."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from . import load_execution_consume_ws_api as consume_ws
from .const import DOMAIN
from .load_action_intent import ACTION_STATE_BLOCKED
from .load_execution_action_snapshot import (
    ActionSnapshotRevalidation,
    revalidate_action_snapshot,
)
from .load_execution_action_snapshot_runtime import action_snapshot_repository
from .load_profiles import profile_by_id

COMMAND_LIST_ACTION_SNAPSHOTS = f"{DOMAIN}/load_execution_action_snapshots/list"
COMMAND_REVALIDATE_ACTION_SNAPSHOT = f"{DOMAIN}/load_execution_action_snapshots/revalidate"
_REGISTERED_KEY = "load_execution_action_snapshot_websocket_registered"


class ActionSnapshotAuditError(ValueError):
    """Raised when a persisted action snapshot cannot be inspected safely."""


async def async_list_execution_action_snapshots(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> dict[str, Any]:
    consume_ws._entry(hass, entry_id)
    snapshots = await action_snapshot_repository(hass, entry_id).async_list()
    return {
        "entry_id": entry_id,
        "snapshots": [snapshot.as_dict() for snapshot in snapshots],
        "execution_performed": False,
        "service_call_performed": False,
        "executor_available": False,
        "read_only": True,
    }


async def async_revalidate_execution_action_snapshot(
    hass: HomeAssistant,
    *,
    entry_id: str,
    attempt_id: str,
) -> dict[str, Any]:
    entry = consume_ws._entry(hass, entry_id)
    if not attempt_id:
        raise ActionSnapshotAuditError("attempt_id is required")

    attempts = await consume_ws._attempt_repository(hass, entry_id).async_list()
    attempt = next((item for item in attempts if item.attempt_id == attempt_id), None)
    if attempt is None:
        raise ActionSnapshotAuditError(f"execution attempt not found: {attempt_id}")

    snapshot = await action_snapshot_repository(hass, entry_id).async_get_by_attempt_id(attempt_id)
    if snapshot is None:
        raise ActionSnapshotAuditError(
            f"immutable action snapshot not found for attempt: {attempt_id}"
        )

    state = hass.states.get(snapshot.entity_id)
    current_state = str(state.state) if state is not None else None

    try:
        profile = profile_by_id(entry.options, snapshot.profile_id)
    except ValueError:
        revalidation = ActionSnapshotRevalidation(
            status=ACTION_STATE_BLOCKED,
            reason="profile_missing",
            current_state=current_state,
            desired_state=snapshot.desired_state,
            attempt_matches=(
                snapshot.attempt_id == attempt.attempt_id
                and snapshot.entry_id == attempt.entry_id
                and snapshot.profile_id == attempt.profile_id
                and snapshot.entity_id == attempt.entity_id
                and snapshot.approval_id == attempt.approval_id
                and snapshot.approval_fingerprint == attempt.approval_fingerprint
                and snapshot.approval_snapshot_digest == attempt.snapshot_digest
            ),
            profile_matches=False,
        )
        profile_payload = None
    else:
        revalidation = revalidate_action_snapshot(
            snapshot,
            attempt=attempt,
            profile=profile,
            current_state=current_state,
        )
        profile_payload = profile.as_dict()

    return {
        "entry_id": entry_id,
        "attempt": attempt.as_dict(),
        "action_snapshot": snapshot.as_dict(),
        "profile": profile_payload,
        "revalidation": revalidation.as_dict(),
        "execution_performed": False,
        "service_call_performed": False,
        "executor_available": False,
        "read_only": True,
    }


@callback
def async_register_load_execution_action_snapshot_websocket(hass: HomeAssistant) -> None:
    """Register admin-only read-only action snapshot commands once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_LIST_ACTION_SNAPSHOTS,
            vol.Required("entry_id"): str,
        }
    )
    async def websocket_list(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_list_execution_action_snapshots(
                hass,
                entry_id=msg["entry_id"],
            )
        except (ActionSnapshotAuditError, consume_ws.ApprovalConsumeError, ValueError) as err:
            connection.send_error(msg["id"], "execution_action_snapshots_invalid", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_action_snapshots_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_REVALIDATE_ACTION_SNAPSHOT,
            vol.Required("entry_id"): str,
            vol.Required("attempt_id"): str,
        }
    )
    async def websocket_revalidate(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_revalidate_execution_action_snapshot(
                hass,
                entry_id=msg["entry_id"],
                attempt_id=msg["attempt_id"],
            )
        except (ActionSnapshotAuditError, consume_ws.ApprovalConsumeError, ValueError) as err:
            connection.send_error(msg["id"], "execution_action_snapshot_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_action_snapshot_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_list)
    websocket_api.async_register_command(hass, websocket_revalidate)
    domain_data[_REGISTERED_KEY] = True
