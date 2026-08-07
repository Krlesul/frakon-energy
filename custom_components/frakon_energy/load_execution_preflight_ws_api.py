"""Administrator-only dry-run execution preflight API for FRAKON Energy.

This API verifies a server-held signed approval, prepares/deduplicates an
execution attempt and returns a proposed Home Assistant service call. It does
not consume the approval and never calls the proposed service.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_approval_ws_api import _record, async_verify_execution_approval
from .load_execution_attempt import ExecutionAttemptLedger
from .load_execution_preflight import prepare_execution_preflight

COMMAND_PREFLIGHT = f"{DOMAIN}/load_execution/preflight"
COMMAND_LIST_ATTEMPTS = f"{DOMAIN}/load_execution/attempts/list"
COMMAND_CANCEL_ATTEMPT = f"{DOMAIN}/load_execution/attempts/cancel"

_LEDGERS_KEY = "load_execution_attempt_ledgers_by_entry"
_REGISTERED_KEY = "load_execution_preflight_websocket_registered"


def _ledger(hass: HomeAssistant, entry_id: str) -> ExecutionAttemptLedger:
    if not entry_id:
        raise ValueError("entry_id is required")
    domain_data = hass.data.setdefault(DOMAIN, {})
    ledgers = domain_data.get(_LEDGERS_KEY)
    if not isinstance(ledgers, dict):
        ledgers = {}
        domain_data[_LEDGERS_KEY] = ledgers
    ledger = ledgers.get(entry_id)
    if isinstance(ledger, ExecutionAttemptLedger):
        return ledger
    ledger = ExecutionAttemptLedger()
    ledgers[entry_id] = ledger
    return ledger


def _attempts_payload(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    ledger = _ledger(hass, entry_id)
    return {
        "entry_id": entry_id,
        "attempts": [attempt.as_dict() for attempt in ledger.list()],
        "runtime_only": True,
        "survives_restart": False,
        "dry_run": True,
        "approval_consumed": False,
        "execution_performed": False,
        "executor_available": False,
        "can_execute": False,
    }


async def async_prepare_execution_preflight(
    hass: HomeAssistant,
    *,
    entry_id: str,
    approval_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify a runtime signed approval and prepare one dry-run attempt."""
    current = now or datetime.now(timezone.utc)
    record = _record(hass, entry_id, approval_id)
    verified = await async_verify_execution_approval(
        hass,
        entry_id=entry_id,
        approval_id=approval_id,
        now=current,
    )
    verification = verified.get("verification")
    evaluation = verified.get("evaluation")
    if not isinstance(verification, dict) or not isinstance(evaluation, dict):
        raise ValueError("approval verification returned incomplete data")

    plan = evaluation.get("plan")
    entity_id = evaluation.get("entity_id")
    if not isinstance(plan, dict):
        result = prepare_execution_preflight(
            _ledger(hass, entry_id),
            approval_id=approval_id,
            snapshot_digest=record.approval.snapshot_digest,
            profile_id=record.profile_id,
            entity_id=str(entity_id) if entity_id else None,
            planned_starts_at=record.plan_starts_at,
            planned_ends_at=record.plan_ends_at,
            approval_valid=False,
            approval_verification_reason=str(verification.get("reason", "invalid")),
            now=current,
        )
    else:
        result = prepare_execution_preflight(
            _ledger(hass, entry_id),
            approval_id=approval_id,
            snapshot_digest=record.approval.snapshot_digest,
            profile_id=record.profile_id,
            entity_id=str(entity_id) if entity_id else None,
            planned_starts_at=str(plan.get("starts_at") or record.plan_starts_at),
            planned_ends_at=str(plan.get("ends_at") or record.plan_ends_at),
            approval_valid=bool(verification.get("valid", False)),
            approval_verification_reason=str(verification.get("reason", "invalid")),
            now=current,
        )

    return {
        "entry_id": entry_id,
        "approval": record.as_dict(now=current),
        "verification": verification,
        "evaluation": evaluation,
        "preflight": result.as_dict(),
        "dry_run": True,
        "approval_consumed": False,
        "execution_performed": False,
        "executor_available": False,
        "can_execute": False,
    }


@callback
def async_register_load_execution_preflight_websocket(hass: HomeAssistant) -> None:
    """Register administrator-only dry-run preflight commands once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_PREFLIGHT,
            vol.Required("entry_id"): str,
            vol.Required("approval_id"): str,
        }
    )
    async def websocket_preflight(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_prepare_execution_preflight(
                hass,
                entry_id=msg["entry_id"],
                approval_id=msg["approval_id"],
            )
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_execution_preflight", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_preflight_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {vol.Required("type"): COMMAND_LIST_ATTEMPTS, vol.Required("entry_id"): str}
    )
    async def websocket_list_attempts(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        connection.send_result(msg["id"], _attempts_payload(hass, msg["entry_id"]))

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_CANCEL_ATTEMPT,
            vol.Required("entry_id"): str,
            vol.Required("attempt_id"): str,
        }
    )
    async def websocket_cancel_attempt(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            attempt = _ledger(hass, msg["entry_id"]).cancel(
                msg["attempt_id"],
                now=datetime.now(timezone.utc),
            )
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_execution_attempt", str(err))
            return
        connection.send_result(
            msg["id"],
            {
                "attempt": attempt.as_dict(),
                "dry_run": True,
                "approval_consumed": False,
                "execution_performed": False,
                "executor_available": False,
                "can_execute": False,
            },
        )

    websocket_api.async_register_command(hass, websocket_preflight)
    websocket_api.async_register_command(hass, websocket_list_attempts)
    websocket_api.async_register_command(hass, websocket_cancel_attempt)
    domain_data[_REGISTERED_KEY] = True
