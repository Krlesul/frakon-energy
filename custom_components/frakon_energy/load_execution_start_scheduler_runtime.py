"""Serialized polling runtime for autonomous bounded starts.

The runtime never creates execution authority. It only asks the durable start
scheduler to scan already-prepared lifecycles once per second. Refreshes never
overlap, and unload waits for an in-flight refresh to settle rather than
cancelling a physical transaction at an unsafe boundary.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN
from .load_execution_start_scheduler import (
    ExecutionStartScheduler,
    async_start_start_scheduler,
    async_stop_start_scheduler,
)

_RUNTIME_KEY = "load_execution_autonomous_start_runtime_by_entry"
_POLL_INTERVAL = timedelta(seconds=1)


class AutonomousStartRuntime:
    """Own one non-overlapping polling loop per config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        if not entry_id:
            raise ValueError("entry_id is required")
        self._hass = hass
        self._entry_id = entry_id
        self._scheduler: ExecutionStartScheduler | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    @property
    def scheduler(self) -> ExecutionStartScheduler | None:
        return self._scheduler

    async def async_start(self) -> ExecutionStartScheduler:
        if self._active and self._scheduler is not None:
            return self._scheduler
        self._scheduler = await async_start_start_scheduler(
            self._hass,
            self._entry_id,
        )
        self._active = True

        @callback
        def interval_tick(_now) -> None:
            self._schedule_refresh()

        self._unsubscribe = async_track_time_interval(
            self._hass,
            interval_tick,
            _POLL_INTERVAL,
        )
        return self._scheduler

    @callback
    def _schedule_refresh(self) -> None:
        if not self._active or self._scheduler is None:
            return
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        task = self._hass.async_create_task(self._async_refresh())
        self._refresh_task = task

        def clear(done: asyncio.Task[None]) -> None:
            if self._refresh_task is done:
                self._refresh_task = None

        task.add_done_callback(clear)

    async def _async_refresh(self) -> None:
        scheduler = self._scheduler
        if not self._active or scheduler is None or not scheduler.started:
            return
        try:
            await scheduler.async_refresh()
        except Exception as err:
            scheduler._healthy = False
            scheduler._last_error = str(err)

    async def async_stop(self) -> None:
        self._active = False
        unsubscribe = self._unsubscribe
        self._unsubscribe = None
        if unsubscribe is not None:
            unsubscribe()

        # Never cancel a refresh task: it may already be inside a persisted
        # physical start transaction. Wait until it reaches a stable outcome.
        task = self._refresh_task
        if task is not None and not task.done():
            try:
                await task
            except Exception:
                pass
        self._refresh_task = None
        await async_stop_start_scheduler(self._hass, self._entry_id)


def _runtime_map(hass: HomeAssistant) -> dict[str, AutonomousStartRuntime]:
    domain_data = hass.data.setdefault(DOMAIN, {})
    value = domain_data.get(_RUNTIME_KEY)
    if not isinstance(value, dict):
        value = {}
        domain_data[_RUNTIME_KEY] = value
    return value


def autonomous_start_runtime(
    hass: HomeAssistant,
    entry_id: str,
) -> AutonomousStartRuntime:
    runtimes = _runtime_map(hass)
    runtime = runtimes.get(entry_id)
    if isinstance(runtime, AutonomousStartRuntime):
        return runtime
    runtime = AutonomousStartRuntime(hass, entry_id)
    runtimes[entry_id] = runtime
    return runtime


async def async_start_autonomous_start_runtime(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionStartScheduler:
    return await autonomous_start_runtime(hass, entry_id).async_start()


async def async_stop_autonomous_start_runtime(
    hass: HomeAssistant,
    entry_id: str,
) -> None:
    runtime = _runtime_map(hass).pop(entry_id, None)
    if isinstance(runtime, AutonomousStartRuntime):
        await runtime.async_stop()
