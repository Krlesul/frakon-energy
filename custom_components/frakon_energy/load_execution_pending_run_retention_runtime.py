"""Best-effort runtime diagnostics for pending-run audit retention."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_pending_run_retention import (
    PENDING_RUN_RETENTION_DAYS,
    PendingRunRetentionResult,
    async_prune_pending_run_audit,
)

_RUNTIME_KEY = "load_execution_pending_run_retention_by_entry"


@dataclass(frozen=True, slots=True)
class PendingRunRetentionRuntimeStatus:
    entry_id: str
    status: str
    retention_days: int
    runs: int
    pruned_total: int
    last_result: dict[str, Any] | None
    last_error: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _statuses(hass: HomeAssistant) -> dict[str, PendingRunRetentionRuntimeStatus]:
    domain_data = hass.data.setdefault(DOMAIN, {})
    value = domain_data.get(_RUNTIME_KEY)
    if not isinstance(value, dict):
        value = {}
        domain_data[_RUNTIME_KEY] = value
    return value


def pending_run_retention_status(
    hass: HomeAssistant,
    entry_id: str,
) -> PendingRunRetentionRuntimeStatus:
    existing = _statuses(hass).get(entry_id)
    if isinstance(existing, PendingRunRetentionRuntimeStatus):
        return existing
    return PendingRunRetentionRuntimeStatus(
        entry_id=entry_id,
        status="not_run",
        retention_days=PENDING_RUN_RETENTION_DAYS,
        runs=0,
        pruned_total=0,
        last_result=None,
        last_error=None,
    )


async def async_run_pending_run_retention_best_effort(
    hass: HomeAssistant,
    *,
    entry_id: str,
    now: datetime | None = None,
) -> PendingRunRetentionRuntimeStatus:
    """Run housekeeping without ever becoming an execution safety dependency."""
    previous = pending_run_retention_status(hass, entry_id)
    try:
        result: PendingRunRetentionResult = await async_prune_pending_run_audit(
            hass,
            entry_id=entry_id,
            now=now,
        )
        status = PendingRunRetentionRuntimeStatus(
            entry_id=entry_id,
            status="ok",
            retention_days=PENDING_RUN_RETENTION_DAYS,
            runs=previous.runs + 1,
            pruned_total=previous.pruned_total + result.pruned,
            last_result=result.as_dict(),
            last_error=None,
        )
    except Exception as err:
        status = PendingRunRetentionRuntimeStatus(
            entry_id=entry_id,
            status="failed_non_blocking",
            retention_days=PENDING_RUN_RETENTION_DAYS,
            runs=previous.runs + 1,
            pruned_total=previous.pruned_total,
            last_result=previous.last_result,
            last_error=str(err),
        )
    _statuses(hass)[entry_id] = status
    return status
