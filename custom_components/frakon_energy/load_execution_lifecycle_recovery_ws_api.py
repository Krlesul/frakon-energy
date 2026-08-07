"""Read-only diagnostics and guarded manual recovery verification for FRAKON Energy."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_lifecycle import (
    CALL_UNKNOWN,
    STATE_DISPATCHED,
    STATE_RECOVERY_REQUIRED,
    STATE_VERIFIED,
    VERIFY_CONFIRMED,
    ExecutionLifecycleError,
    verify_desired_state,
)
from .load_execution_lifecycle_recovery import (
    LifecycleRecoveryBlockedError,
    assert_lifecycle_recovery_ready,
    lifecycle_recovery_summary,
    recovery_diagnostic_for_record,
)
from .load_execution_lifecycle_runtime import lifecycle_repository

COMMAND_RECOVERY_DIAGNOSTICS = f"{DOMAIN}/load_execution_lifecycle/recovery"
COMMAND_VERIFY_RECOVERY = f"{DOMAIN}/load_execution_lifecycle/recovery_verify"
_REGISTERED_KEY = "load_execution_lifecycle_recovery_websocket_registered"


class RecoveryResolutionError(ValueError):
    """Raised when a recovery-required lifecycle cannot be manually verified."""


def _live_state(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    return str(state.state) if state is not None else None


def _current_counts(records: tuple[Any, ...]) -> dict[str, int]:
    return {
        "recovery_required": sum(record.state == STATE_RECOVERY_REQUIRED for record in records),
        "dispatched_pending_verification": sum(record.state == STATE_DISPATCHED for record in records),
        "verified_unknown_call": sum(
            record.state == STATE_VERIFIED and record.service_call_status == CALL_UNKNOWN
            for record in records
        ),
    }


async def async_lifecycle_recovery_diagnostics(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> dict[str, Any]:
    """Return startup recovery status and live entity evidence without mutation."""
    if not entry_id:
        raise ValueError("entry_id is required")
    summary = lifecycle_recovery_summary(hass, entry_id)
    records = await lifecycle_repository(hass, entry_id).async_list()
    diagnostics: list[dict[str, Any]] = []
    for record in records:
        diagnostics.append(
            recovery_diagnostic_for_record(
                record,
                current_state=_live_state(hass, record.entity_id),
            )
        )
    return {
        "entry_id": entry_id,
        "recovery": summary.as_dict(),
        "current": _current_counts(records),
        "lifecycles": diagnostics,
        "manual_review_required": any(
            item["diagnostic"] == "manual_recovery_review_required"
            for item in diagnostics
        ),
        "read_only": True,
        "state_transition_performed": False,
        "execution_performed": False,
        "executor_available": False,
    }


async def async_verify_recovery_lifecycle(
    hass: HomeAssistant,
    *,
    entry_id: str,
    attempt_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Manually resolve recovery only after live desired-state verification."""
    if not entry_id or not attempt_id:
        raise RecoveryResolutionError("entry_id and attempt_id are required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise RecoveryResolutionError("now must be timezone-aware")
    assert_lifecycle_recovery_ready(hass, entry_id)

    repository = lifecycle_repository(hass, entry_id)
    record = await repository.async_get_by_attempt_id(attempt_id)
    if record is None:
        raise RecoveryResolutionError(f"execution lifecycle not found: {attempt_id}")

    current_state = _live_state(hass, record.entity_id)
    normalized = current_state.strip().lower() if isinstance(current_state, str) else None

    # Exact retry of an already resolved unknown-outcome recovery is inert.
    if (
        record.state == STATE_VERIFIED
        and record.service_call_status == CALL_UNKNOWN
        and record.verification_status == VERIFY_CONFIRMED
    ):
        return {
            "lifecycle": record.as_dict(),
            "current_state": normalized,
            "desired_state_observed": normalized == record.desired_state,
            "recovery_resolved": True,
            "state_transition_performed": False,
            "idempotent_replay": True,
            "service_call_performed": None,
            "execution_performed": False,
            "executor_available": False,
        }

    if record.state != STATE_RECOVERY_REQUIRED:
        raise RecoveryResolutionError(
            f"lifecycle is not recovery_required: {record.state}"
        )
    if normalized != record.desired_state:
        raise RecoveryResolutionError(
            "current entity state does not match the immutable desired state"
        )

    verified = verify_desired_state(
        record,
        current_state=current_state,
        now=max(int(current.timestamp()), record.updated_at),
    )
    updated = await repository.async_update(verified)
    return {
        "lifecycle": updated.as_dict(),
        "current_state": normalized,
        "desired_state_observed": True,
        "recovery_resolved": True,
        "state_transition_performed": True,
        "idempotent_replay": False,
        # Unknown remains unknown: observed desired state does not prove who caused it.
        "service_call_performed": None,
        "execution_performed": False,
        "executor_available": False,
    }


@callback
def async_register_load_execution_lifecycle_recovery_websocket(
    hass: HomeAssistant,
) -> None:
    """Register administrator-only recovery diagnostics/resolution once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_RECOVERY_DIAGNOSTICS,
            vol.Required("entry_id"): str,
        }
    )
    async def websocket_recovery(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_lifecycle_recovery_diagnostics(
                hass,
                entry_id=msg["entry_id"],
            )
        except ValueError as err:
            connection.send_error(msg["id"], "execution_lifecycle_recovery_invalid", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_lifecycle_recovery_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_VERIFY_RECOVERY,
            vol.Required("entry_id"): str,
            vol.Required("attempt_id"): str,
        }
    )
    async def websocket_verify_recovery(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_verify_recovery_lifecycle(
                hass,
                entry_id=msg["entry_id"],
                attempt_id=msg["attempt_id"],
            )
        except LifecycleRecoveryBlockedError as err:
            connection.send_error(msg["id"], "execution_lifecycle_recovery_blocked", str(err))
            return
        except (RecoveryResolutionError, ExecutionLifecycleError, ValueError) as err:
            connection.send_error(msg["id"], "execution_lifecycle_recovery_verify_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_lifecycle_recovery_verify_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_recovery)
    websocket_api.async_register_command(hass, websocket_verify_recovery)
    domain_data[_REGISTERED_KEY] = True
