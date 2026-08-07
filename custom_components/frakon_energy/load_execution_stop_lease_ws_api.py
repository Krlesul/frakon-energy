"""Administrator-only durable stop-obligation preparation for FRAKON Energy."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_dispatch_gate import DISPATCH_GATE_READY
from .load_execution_dispatch_gate_ws_api import async_execution_dispatch_gate
from .load_execution_lifecycle import STATE_PREPARED
from .load_execution_lifecycle_recovery import LifecycleRecoveryBlockedError
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_execution_stop_lease import (
    ExecutionStopLease,
    StopLeaseConflictError,
    StopLeaseError,
)
from .load_execution_stop_lease_runtime import stop_lease_repository

COMMAND_PREPARE_STOP_LEASE = f"{DOMAIN}/load_execution_stop_lease/prepare"
COMMAND_LIST_STOP_LEASES = f"{DOMAIN}/load_execution_stop_lease/list"
_REGISTERED_KEY = "load_execution_stop_lease_websocket_registered"
_LOCKS_KEY = "load_execution_stop_lease_prepare_locks_by_entry"


class StopLeasePrepareError(ValueError):
    """Raised when a stop obligation cannot be safely prepared."""


def _transaction_lock(hass: HomeAssistant, entry_id: str) -> asyncio.Lock:
    domain_data = hass.data.setdefault(DOMAIN, {})
    locks = domain_data.get(_LOCKS_KEY)
    if not isinstance(locks, dict):
        locks = {}
        domain_data[_LOCKS_KEY] = locks
    lock = locks.get(entry_id)
    if isinstance(lock, asyncio.Lock):
        return lock
    lock = asyncio.Lock()
    locks[entry_id] = lock
    return lock


async def async_prepare_stop_lease(
    hass: HomeAssistant,
    *,
    entry_id: str,
    attempt_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist the exact turn-off obligation before any future start dispatch."""
    if not entry_id or not attempt_id:
        raise StopLeasePrepareError("entry_id and attempt_id are required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise StopLeasePrepareError("now must be timezone-aware")

    async with _transaction_lock(hass, entry_id):
        lifecycles = lifecycle_repository(hass, entry_id)
        lifecycle = await lifecycles.async_get_by_attempt_id(attempt_id)
        if lifecycle is None:
            raise StopLeasePrepareError(
                f"execution lifecycle not found: {attempt_id}"
            )

        leases = stop_lease_repository(hass, entry_id)
        existing = await leases.async_get_by_lifecycle_id(lifecycle.lifecycle_id)
        if existing is not None:
            return {
                "stop_lease": existing.as_dict(),
                "created": False,
                "idempotent_replay": True,
                "stop_obligation_armed": True,
                "can_start_without_stop_lease": False,
                "service_call_performed": False,
                "execution_performed": False,
                "executor_available": False,
            }

        gate_payload = await async_execution_dispatch_gate(
            hass,
            entry_id=entry_id,
            attempt_id=attempt_id,
            now=current,
        )
        gate = gate_payload.get("dispatch_gate")
        if not isinstance(gate, dict):
            raise StopLeasePrepareError("dispatch gate response is invalid")
        if gate.get("status") != DISPATCH_GATE_READY or gate.get("can_dispatch") is not True:
            raise StopLeasePrepareError(
                f"dispatch gate is not ready for a bounded start: {gate.get('status')}/{gate.get('reason')}"
            )

        # Re-read the durable lifecycle after gate evaluation before binding the lease.
        lifecycle = await lifecycles.async_get_by_attempt_id(attempt_id)
        if lifecycle is None or lifecycle.state != STATE_PREPARED:
            raise StopLeasePrepareError("lifecycle changed while preparing stop lease")
        if lifecycle.lifecycle_id != gate.get("lifecycle_id"):
            raise StopLeasePrepareError("dispatch gate lifecycle identity changed")

        lease = ExecutionStopLease.from_prepared_lifecycle(
            lifecycle,
            created_at=max(int(current.timestamp()), lifecycle.created_at),
        )
        result = await leases.async_record(lease)
        return {
            **result.as_dict(),
            "dispatch_gate": gate,
            "stop_obligation_armed": True,
            "can_start_without_stop_lease": False,
            "execution_performed": False,
        }


async def async_list_stop_leases(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> dict[str, Any]:
    if not entry_id:
        raise StopLeasePrepareError("entry_id is required")
    leases = await stop_lease_repository(hass, entry_id).async_list()
    return {
        "entry_id": entry_id,
        "stop_leases": [lease.as_dict() for lease in leases],
        "read_only": True,
        "service_call_performed": False,
        "execution_performed": False,
        "executor_available": False,
    }


@callback
def async_register_load_execution_stop_lease_websocket(hass: HomeAssistant) -> None:
    """Register stop-obligation prepare/list commands once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_PREPARE_STOP_LEASE,
            vol.Required("entry_id"): str,
            vol.Required("attempt_id"): str,
        }
    )
    async def websocket_prepare(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_prepare_stop_lease(
                hass,
                entry_id=msg["entry_id"],
                attempt_id=msg["attempt_id"],
            )
        except LifecycleRecoveryBlockedError as err:
            connection.send_error(msg["id"], "execution_lifecycle_recovery_blocked", str(err))
            return
        except StopLeaseConflictError as err:
            connection.send_error(msg["id"], "execution_stop_lease_conflict", str(err))
            return
        except (StopLeasePrepareError, StopLeaseError, ValueError) as err:
            connection.send_error(msg["id"], "execution_stop_lease_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_stop_lease_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_LIST_STOP_LEASES,
            vol.Required("entry_id"): str,
        }
    )
    async def websocket_list(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_list_stop_leases(hass, entry_id=msg["entry_id"])
        except (StopLeasePrepareError, StopLeaseError, ValueError) as err:
            connection.send_error(msg["id"], "execution_stop_lease_list_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_stop_lease_list_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_prepare)
    websocket_api.async_register_command(hass, websocket_list)
    domain_data[_REGISTERED_KEY] = True
