"""Transactional lifecycle for FRAKON Energy execution background runtimes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from homeassistant.core import HomeAssistant

from .entry_runtime_cache import purge_entry_scoped_domain_caches
from .load_execution_pending_run_retention_runtime import (
    async_run_pending_run_retention_best_effort,
)
from .load_execution_pending_run_scheduler import (
    async_start_pending_run_scheduler,
    async_stop_pending_run_scheduler,
)
from .load_execution_phase_settlement_runtime import (
    async_start_phase_settlement_runtime,
    async_stop_phase_settlement_runtime,
)
from .load_execution_start_scheduler import (
    async_start_start_scheduler,
    async_stop_start_scheduler,
)
from .load_execution_stop_scheduler import (
    async_start_stop_scheduler,
    async_stop_stop_scheduler,
)

RuntimeStopper = Callable[[HomeAssistant, str], Awaitable[None]]


async def _async_rollback_started(
    hass: HomeAssistant,
    entry_id: str,
    started_stoppers: list[RuntimeStopper],
) -> None:
    """Best-effort rollback without replacing the original startup failure."""
    for stopper in reversed(started_stoppers):
        try:
            await stopper(hass, entry_id)
        except Exception:
            # A failed cleanup must not hide the original setup exception. Any
            # surviving runtime remains fail-safe and will be retried on reload.
            pass


async def async_start_execution_runtimes(hass: HomeAssistant, entry_id: str) -> None:
    """Start execution workers atomically from the config-entry perspective."""
    if not entry_id:
        raise ValueError("entry_id is required")

    started_stoppers: list[RuntimeStopper] = []
    try:
        await async_start_stop_scheduler(hass, entry_id)
        started_stoppers.append(async_stop_stop_scheduler)

        await async_start_start_scheduler(hass, entry_id)
        started_stoppers.append(async_stop_start_scheduler)

        # Housekeeping is deliberately best-effort and cannot block execution setup.
        await async_run_pending_run_retention_best_effort(hass, entry_id=entry_id)

        await async_start_pending_run_scheduler(hass, entry_id)
        started_stoppers.append(async_stop_pending_run_scheduler)

        await async_start_phase_settlement_runtime(hass, entry_id)
        started_stoppers.append(async_stop_phase_settlement_runtime)
    except Exception:
        await _async_rollback_started(hass, entry_id, started_stoppers)
        raise


async def async_stop_execution_runtimes(hass: HomeAssistant, entry_id: str) -> None:
    """Stop all execution workers in reverse startup order and drop entry caches.

    Every stopper is attempted even when an earlier one fails. Entry-scoped in-memory
    repository/runtime wrappers are then purged so a reload reconstructs them from
    durable Home Assistant storage. After cleanup is exhausted, re-raise the first
    failure so callers still know unload was not fully clean.
    """
    if not entry_id:
        raise ValueError("entry_id is required")

    first_error: Exception | None = None
    for stopper in (
        async_stop_phase_settlement_runtime,
        async_stop_pending_run_scheduler,
        async_stop_start_scheduler,
        async_stop_stop_scheduler,
    ):
        try:
            await stopper(hass, entry_id)
        except Exception as err:
            if first_error is None:
                first_error = err

    purge_entry_scoped_domain_caches(hass, entry_id)

    if first_error is not None:
        raise first_error
