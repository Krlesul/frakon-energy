"""Runtime-only WebSocket API for FRAKON Energy execution approvals.

This API can request, approve, revoke and validate short-lived approvals. It
intentionally exposes no execute/consume command and performs no service call.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_approval import (
    APPROVAL_TTL_DEFAULT_SECONDS,
    APPROVAL_TTL_MAX_SECONDS,
    APPROVAL_TTL_MIN_SECONDS,
    LoadExecutionApproval,
    LoadExecutionApprovalRegistry,
    approval_scope_from_evaluation,
)
from .load_execution_policy_ws_api import _parse_datetime, async_evaluate_profile_execution

COMMAND_LIST = f"{DOMAIN}/load_execution_approvals/list"
COMMAND_REQUEST = f"{DOMAIN}/load_execution_approvals/request"
COMMAND_APPROVE = f"{DOMAIN}/load_execution_approvals/approve"
COMMAND_REVOKE = f"{DOMAIN}/load_execution_approvals/revoke"
COMMAND_VALIDATE = f"{DOMAIN}/load_execution_approvals/validate"

_REGISTRY_KEY = "load_execution_approval_registry"
_REGISTERED_KEY = "load_execution_approval_websocket_registered"


def _registry(hass: HomeAssistant) -> LoadExecutionApprovalRegistry:
    domain_data = hass.data.setdefault(DOMAIN, {})
    registry = domain_data.get(_REGISTRY_KEY)
    if isinstance(registry, LoadExecutionApprovalRegistry):
        return registry
    registry = LoadExecutionApprovalRegistry()
    domain_data[_REGISTRY_KEY] = registry
    return registry


def _approval_for_entry(
    registry: LoadExecutionApprovalRegistry,
    *,
    approval_id: str,
    entry_id: str,
) -> LoadExecutionApproval:
    approval = registry.get(approval_id)
    if approval.scope.entry_id != entry_id:
        raise ValueError("approval does not belong to this FRAKON Energy config entry")
    return approval


def _approval_payload(approval: LoadExecutionApproval) -> dict[str, Any]:
    payload = approval.as_dict()
    payload.update(
        {
            "executor_available": False,
            "execution_performed": False,
            "can_execute": False,
        }
    )
    return payload


def _list_payload(registry: LoadExecutionApprovalRegistry, entry_id: str) -> dict[str, Any]:
    return {
        "entry_id": entry_id,
        "approvals": [_approval_payload(item) for item in registry.list(entry_id=entry_id)],
        "runtime_only": True,
        "survives_restart": False,
        "executor_available": False,
        "execution_performed": False,
        "can_execute": False,
    }


async def _fresh_evaluation_for_approval(
    hass: HomeAssistant,
    approval: LoadExecutionApproval,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Re-evaluate the exact stored plan window before approval/validation."""
    starts_at = datetime.fromisoformat(approval.scope.plan_starts_at)
    ends_at = datetime.fromisoformat(approval.scope.plan_ends_at)
    return await async_evaluate_profile_execution(
        hass,
        entry_id=approval.scope.entry_id,
        profile_id=approval.scope.profile_id,
        earliest_start=starts_at,
        deadline=ends_at,
        now=now,
    )


def _scope_hash_from_evaluation(entry_id: str, evaluation: dict[str, Any]) -> str | None:
    if evaluation.get("status") != "approval_required":
        return None
    try:
        return approval_scope_from_evaluation(entry_id, evaluation).fingerprint()
    except (ValueError, TypeError, KeyError):
        return None


async def async_request_approval(
    hass: HomeAssistant,
    *,
    entry_id: str,
    profile_id: str,
    ttl_seconds: int = APPROVAL_TTL_DEFAULT_SECONDS,
    earliest_start: datetime | None = None,
    deadline: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create a pending approval only when the current policy verdict allows it."""
    evaluation = await async_evaluate_profile_execution(
        hass,
        entry_id=entry_id,
        profile_id=profile_id,
        earliest_start=earliest_start,
        deadline=deadline,
        now=now,
    )
    if evaluation.get("status") != "approval_required":
        return {
            "created": False,
            "approval": None,
            "evaluation": evaluation,
            "runtime_only": True,
            "survives_restart": False,
            "executor_available": False,
            "execution_performed": False,
            "can_execute": False,
        }

    approval = _registry(hass).create(
        entry_id,
        evaluation,
        ttl_seconds=ttl_seconds,
        now=now,
    )
    return {
        "created": True,
        "approval": _approval_payload(approval),
        "evaluation": evaluation,
        "runtime_only": True,
        "survives_restart": False,
        "executor_available": False,
        "execution_performed": False,
        "can_execute": False,
    }


async def async_approve_request(
    hass: HomeAssistant,
    *,
    entry_id: str,
    approval_id: str,
    approved_by: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Approve a request only while its exact immutable scope still matches."""
    registry = _registry(hass)
    approval = _approval_for_entry(registry, approval_id=approval_id, entry_id=entry_id)
    evaluation = await _fresh_evaluation_for_approval(hass, approval, now=now)
    scope_hash = _scope_hash_from_evaluation(entry_id, evaluation)
    if scope_hash is None:
        raise ValueError("approval scope is no longer eligible under the current execution policy")
    updated = registry.approve(
        approval_id,
        current_scope_hash=scope_hash,
        approved_by=approved_by,
        now=now,
    )
    return {
        "approval": _approval_payload(updated),
        "evaluation": evaluation,
        "executor_available": False,
        "execution_performed": False,
        "can_execute": False,
    }


async def async_validate_request(
    hass: HomeAssistant,
    *,
    entry_id: str,
    approval_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate an approval against a fresh scope without consuming it."""
    registry = _registry(hass)
    approval = _approval_for_entry(registry, approval_id=approval_id, entry_id=entry_id)
    evaluation = await _fresh_evaluation_for_approval(hass, approval, now=now)
    scope_hash = _scope_hash_from_evaluation(entry_id, evaluation) or "current-scope-is-blocked"
    validation = registry.validate(
        approval_id,
        current_scope_hash=scope_hash,
        now=now,
    )
    return {
        "approval": _approval_payload(registry.get(approval_id)),
        "validation": validation.as_dict(),
        "evaluation": evaluation,
        "executor_available": False,
        "execution_performed": False,
        "can_execute": False,
    }


@callback
def async_register_load_execution_approval_websocket(hass: HomeAssistant) -> None:
    """Register runtime-only approval commands once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command(
        {vol.Required("type"): COMMAND_LIST, vol.Required("entry_id"): str}
    )
    @websocket_api.async_response
    async def websocket_list(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        connection.send_result(msg["id"], _list_payload(_registry(hass), msg["entry_id"]))

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_REQUEST,
            vol.Required("entry_id"): str,
            vol.Required("profile_id"): str,
            vol.Optional("ttl_seconds", default=APPROVAL_TTL_DEFAULT_SECONDS): vol.All(
                int, vol.Range(min=APPROVAL_TTL_MIN_SECONDS, max=APPROVAL_TTL_MAX_SECONDS)
            ),
            vol.Optional("earliest_start"): str,
            vol.Optional("deadline"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_request(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_request_approval(
                hass,
                entry_id=msg["entry_id"],
                profile_id=msg["profile_id"],
                ttl_seconds=msg["ttl_seconds"],
                earliest_start=_parse_datetime(msg.get("earliest_start"), "earliest_start"),
                deadline=_parse_datetime(msg.get("deadline"), "deadline"),
            )
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_load_execution_approval_request", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "load_execution_approval_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_APPROVE,
            vol.Required("entry_id"): str,
            vol.Required("approval_id"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_approve(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        user = getattr(connection, "user", None)
        approved_by = str(getattr(user, "id", None) or "authenticated-user")
        try:
            result = await async_approve_request(
                hass,
                entry_id=msg["entry_id"],
                approval_id=msg["approval_id"],
                approved_by=approved_by,
            )
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_load_execution_approval", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "load_execution_approval_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_REVOKE,
            vol.Required("entry_id"): str,
            vol.Required("approval_id"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_revoke(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            registry = _registry(hass)
            _approval_for_entry(registry, approval_id=msg["approval_id"], entry_id=msg["entry_id"])
            updated = registry.revoke(msg["approval_id"])
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_load_execution_approval", str(err))
            return
        connection.send_result(
            msg["id"],
            {
                "approval": _approval_payload(updated),
                "executor_available": False,
                "execution_performed": False,
                "can_execute": False,
            },
        )

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_VALIDATE,
            vol.Required("entry_id"): str,
            vol.Required("approval_id"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_validate(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_validate_request(
                hass,
                entry_id=msg["entry_id"],
                approval_id=msg["approval_id"],
            )
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_load_execution_approval", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "load_execution_approval_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_list)
    websocket_api.async_register_command(hass, websocket_request)
    websocket_api.async_register_command(hass, websocket_approve)
    websocket_api.async_register_command(hass, websocket_revoke)
    websocket_api.async_register_command(hass, websocket_validate)
    domain_data[_REGISTERED_KEY] = True
