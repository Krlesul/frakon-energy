"""Home Assistant runtime scheduler for weekly active-tariff source checks."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .tariff_update_ha import async_check_active_tariff_source_if_due_ha

_LOGGER = logging.getLogger(__name__)

_RUNTIME_KEY = "tariff_update_runtimes_by_entry"
DEFAULT_TARIFF_UPDATE_PROBE_INTERVAL = timedelta(hours=6)


class TariffUpdateRuntime:
    """Small periodic probe around the durable seven-day tariff cadence gate.

    The in-memory timer is never pricing authority and never decides whether a
    network request is allowed. It merely asks the durable cadence gate several
    times per day so HA restarts cannot postpone a due weekly check indefinitely.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        probe_interval: timedelta = DEFAULT_TARIFF_UPDATE_PROBE_INTERVAL,
    ) -> None:
        if not isinstance(probe_interval, timedelta) or probe_interval <= timedelta(0):
            raise ValueError("probe_interval must be a positive timedelta")
        self._hass = hass
        self._entry = entry
        self._probe_interval = probe_interval
        self._started = False
        self._unsubscribe: Any | None = None
        self._probe_task: asyncio.Task[Any] | None = None
        self._last_probe_at = None
        self._last_check_status: str | None = None
        self._last_error: str | None = None

    @property
    def entry_id(self) -> str:
        return self._entry.entry_id

    @property
    def started(self) -> bool:
        return self._started

    @property
    def probe_interval(self) -> timedelta:
        return self._probe_interval

    @property
    def last_probe_at(self):
        return self._last_probe_at

    @property
    def last_check_status(self) -> str | None:
        return self._last_check_status

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def async_start(self) -> None:
        """Start the periodic probe and schedule one immediate due evaluation."""
        if self._started:
            return
        self._started = True
        try:
            self._unsubscribe = async_track_time_interval(
                self._hass,
                self._interval_fired,
                self._probe_interval,
                name=f"FRAKON Energy tariff update probe {self.entry_id}",
            )
            self._schedule_probe()
        except Exception:
            self.stop()
            raise

    @callback
    def stop(self) -> None:
        """Synchronously stop timers/tasks; safe for ConfigEntry unload callbacks."""
        self._started = False
        if self._unsubscribe is not None:
            unsubscribe = self._unsubscribe
            self._unsubscribe = None
            unsubscribe()

        task = self._probe_task
        self._probe_task = None
        if task is not None and not task.done():
            task.cancel()

    async def async_stop(self) -> None:
        """Stop future probes and await cancellation of any in-flight task."""
        task = self._probe_task
        self.stop()
        if task is not None and not task.done():
            try:
                await task
            except asyncio.CancelledError:
                pass

    @callback
    def _interval_fired(self, _now) -> None:
        if not self._started:
            return
        self._schedule_probe()

    def _schedule_probe(self) -> None:
        if not self._started:
            return
        task = self._probe_task
        if task is not None and not task.done():
            return
        self._probe_task = self._hass.async_create_task(self._async_probe())

    async def _async_probe(self) -> None:
        now = dt_util.now()
        self._last_probe_at = now
        try:
            run = await async_check_active_tariff_source_if_due_ha(
                self._hass,
                self._entry,
                day=now.date(),
                checked_at=now,
            )
        except LookupError:
            # No confirmed active contract/all-in tariff yet. This is a normal
            # pre-configuration state, not a runtime failure.
            self._last_check_status = None
            self._last_error = None
            return
        except ValueError as err:
            # Corrupt or inconsistent confirmed pricing state must stay
            # fail-closed. Keep the integration alive and surface diagnostics.
            self._last_check_status = "error"
            self._last_error = str(err)
            _LOGGER.warning(
                "FRAKON Energy tariff update state is invalid for entry %s: %s",
                self.entry_id,
                err,
            )
            return
        except Exception as err:  # pragma: no cover - defensive HA runtime boundary
            self._last_check_status = "error"
            self._last_error = str(err)
            _LOGGER.exception(
                "Unexpected FRAKON Energy tariff update runtime failure for entry %s",
                self.entry_id,
            )
            return

        self._last_error = None
        if run is None:
            self._last_check_status = "not_due"
        else:
            self._last_check_status = run.check.status


def _runtime_registry(hass: HomeAssistant) -> dict[str, TariffUpdateRuntime]:
    domain_data = hass.data.setdefault(DOMAIN, {})
    registry = domain_data.get(_RUNTIME_KEY)
    if isinstance(registry, dict):
        return registry
    registry = {}
    domain_data[_RUNTIME_KEY] = registry
    return registry


async def async_start_tariff_update_runtime(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> TariffUpdateRuntime:
    """Start exactly one tariff-update runtime for a config entry."""
    registry = _runtime_registry(hass)
    existing = registry.get(entry.entry_id)
    if isinstance(existing, TariffUpdateRuntime):
        await existing.async_start()
        return existing

    runtime = TariffUpdateRuntime(hass, entry)
    registry[entry.entry_id] = runtime
    try:
        await runtime.async_start()
    except Exception:
        registry.pop(entry.entry_id, None)
        raise

    @callback
    def stop_on_entry_unload() -> None:
        if registry.get(entry.entry_id) is runtime:
            registry.pop(entry.entry_id, None)
        runtime.stop()

    entry.async_on_unload(stop_on_entry_unload)
    return runtime


async def async_stop_tariff_update_runtime(
    hass: HomeAssistant,
    entry_id: str,
) -> None:
    """Stop and forget one config entry's tariff-update runtime."""
    registry = _runtime_registry(hass)
    runtime = registry.pop(entry_id, None)
    if isinstance(runtime, TariffUpdateRuntime):
        await runtime.async_stop()
