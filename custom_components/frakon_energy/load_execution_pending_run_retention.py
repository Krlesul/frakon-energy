"""Conservative retention for redundant FRAKON Energy pending-run audit copies.

Pending-run records are only scheduling copies. Durable execution lifecycle,
stop lease and stop lifecycle ledgers remain separate and are never touched by
this retention module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from .load_execution_lifecycle import (
    STATE_CANCELLED,
    STATE_FAILED,
    STATE_VERIFIED,
)
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_execution_pending_run import ExecutionPendingRun
from .load_execution_pending_run_runtime import pending_run_repository

PENDING_RUN_RETENTION_DAYS = 90
_TERMINAL_START_STATES = {STATE_CANCELLED, STATE_FAILED, STATE_VERIFIED}


@dataclass(frozen=True, slots=True)
class PendingRunRetentionResult:
    entry_id: str
    scanned: int
    eligible: int
    pruned: int
    retained_active: int
    retained_young: int
    retention_days: int = PENDING_RUN_RETENTION_DAYS
    service_call_performed: bool = False
    execution_performed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("pending run plan timestamp must be timezone-aware")
    return parsed


def _old_enough(record: ExecutionPendingRun, now: datetime) -> bool:
    retention_deadline = _aware(record.plan.ends_at) + timedelta(
        days=PENDING_RUN_RETENTION_DAYS
    )
    return now >= retention_deadline


async def async_prune_pending_run_audit(
    hass: HomeAssistant,
    *,
    entry_id: str,
    now: datetime | None = None,
) -> PendingRunRetentionResult:
    """Remove only old redundant scheduling copies, never active/recovery work."""
    if not entry_id:
        raise ValueError("entry_id is required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    pending_repository = pending_run_repository(hass, entry_id)
    lifecycle_repo = lifecycle_repository(hass, entry_id)
    records = await pending_repository.async_list()
    eligible_attempts: set[str] = set()
    retained_active = 0
    retained_young = 0

    for record in records:
        if not _old_enough(record, current):
            retained_young += 1
            continue
        lifecycle = await lifecycle_repo.async_get_by_attempt_id(record.attempt_id)
        if lifecycle is None or lifecycle.state in _TERMINAL_START_STATES:
            eligible_attempts.add(record.attempt_id)
            continue
        # prepared, dispatching, dispatched and recovery_required are all
        # deliberately retained regardless of age. They may still be relevant
        # to safety/recovery and retention must never influence them.
        retained_active += 1

    result = await pending_repository.async_remove_attempt_ids(eligible_attempts)
    return PendingRunRetentionResult(
        entry_id=entry_id,
        scanned=len(records),
        eligible=len(eligible_attempts),
        pruned=len(result.removed),
        retained_active=retained_active,
        retained_young=retained_young,
    )
