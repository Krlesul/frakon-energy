"""Cross-store ownership proof for bounded start recovery.

A recovered/confirmed start must not be marked verified unless its exact durable
stop lease and stop lifecycle already exist. This prevents a crash between
persisting start ``dispatching`` and persisting stop ownership from producing a
verified unbounded run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from homeassistant.core import HomeAssistant

from .load_execution_lifecycle import ExecutionLifecycleRecord
from .load_execution_stop_lease import ExecutionStopLease
from .load_execution_stop_lease_runtime import stop_lease_repository
from .load_execution_stop_lifecycle import ExecutionStopLifecycleRecord
from .load_execution_stop_lifecycle_runtime import stop_lifecycle_repository


@dataclass(frozen=True, slots=True)
class StartStopOwnershipProof:
    start_lifecycle_id: str
    stop_lease_present: bool
    stop_lifecycle_present: bool
    stop_lease_matches: bool
    stop_lifecycle_matches: bool
    ownership_ready: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _lease_matches_start(
    start: ExecutionLifecycleRecord,
    lease: ExecutionStopLease,
) -> bool:
    try:
        start.validated()
        lease.validated()
    except ValueError:
        return False
    return (
        lease.entry_id == start.entry_id
        and lease.lifecycle_id == start.lifecycle_id
        and lease.attempt_id == start.attempt_id
        and lease.action_snapshot_id == start.action_snapshot_id
        and lease.profile_id == start.profile_id
        and lease.entity_id == start.entity_id
        and lease.approval_snapshot_digest == start.approval_snapshot_digest
        and lease.plan_digest == start.plan_digest
        and lease.starts_at == start.plan.starts_at
        and lease.ends_at == start.plan.ends_at
    )


def _stop_lifecycle_matches(
    start: ExecutionLifecycleRecord,
    lease: ExecutionStopLease,
    stop: ExecutionStopLifecycleRecord,
) -> bool:
    try:
        stop.validated()
    except ValueError:
        return False
    return (
        stop.lease_id == lease.lease_id
        and stop.entry_id == start.entry_id
        and stop.start_lifecycle_id == start.lifecycle_id
        and stop.attempt_id == start.attempt_id
        and stop.action_snapshot_id == start.action_snapshot_id
        and stop.profile_id == start.profile_id
        and stop.entity_id == start.entity_id
        and stop.approval_snapshot_digest == start.approval_snapshot_digest
        and stop.plan_digest == start.plan_digest
        and stop.starts_at == start.plan.starts_at
        and stop.ends_at == start.plan.ends_at
        and stop.service_domain == lease.service_domain
        and stop.service_name == lease.service_name
        and stop.desired_state == lease.desired_state == "off"
    )


async def async_start_stop_ownership_proof(
    hass: HomeAssistant,
    *,
    entry_id: str,
    start: ExecutionLifecycleRecord,
) -> StartStopOwnershipProof:
    """Read exact durable stop ownership for one start lifecycle."""
    start.validated()
    if entry_id != start.entry_id:
        return StartStopOwnershipProof(
            start_lifecycle_id=start.lifecycle_id,
            stop_lease_present=False,
            stop_lifecycle_present=False,
            stop_lease_matches=False,
            stop_lifecycle_matches=False,
            ownership_ready=False,
            reason="entry_id_mismatch",
        )

    lease = await stop_lease_repository(hass, entry_id).async_get_by_lifecycle_id(
        start.lifecycle_id
    )
    if lease is None:
        return StartStopOwnershipProof(
            start_lifecycle_id=start.lifecycle_id,
            stop_lease_present=False,
            stop_lifecycle_present=False,
            stop_lease_matches=False,
            stop_lifecycle_matches=False,
            ownership_ready=False,
            reason="stop_lease_missing",
        )
    lease_matches = _lease_matches_start(start, lease)
    if not lease_matches:
        return StartStopOwnershipProof(
            start_lifecycle_id=start.lifecycle_id,
            stop_lease_present=True,
            stop_lifecycle_present=False,
            stop_lease_matches=False,
            stop_lifecycle_matches=False,
            ownership_ready=False,
            reason="stop_lease_binding_mismatch",
        )

    stop = await stop_lifecycle_repository(
        hass,
        entry_id,
    ).async_get_by_start_lifecycle_id(start.lifecycle_id)
    if stop is None:
        return StartStopOwnershipProof(
            start_lifecycle_id=start.lifecycle_id,
            stop_lease_present=True,
            stop_lifecycle_present=False,
            stop_lease_matches=True,
            stop_lifecycle_matches=False,
            ownership_ready=False,
            reason="stop_lifecycle_missing",
        )
    stop_matches = _stop_lifecycle_matches(start, lease, stop)
    return StartStopOwnershipProof(
        start_lifecycle_id=start.lifecycle_id,
        stop_lease_present=True,
        stop_lifecycle_present=True,
        stop_lease_matches=True,
        stop_lifecycle_matches=stop_matches,
        ownership_ready=stop_matches,
        reason="stop_ownership_ready" if stop_matches else "stop_lifecycle_binding_mismatch",
    )
