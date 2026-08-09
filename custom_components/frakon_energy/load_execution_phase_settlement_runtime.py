"""Best-effort runtime that settles already-started phase reservations.

The runtime never calls Home Assistant services and never creates execution
authority. It may only advance settlement confirmation for already verified
lifecycles and release reservations through the separately proof-gated release
primitive. Any error leaves the reservation intact until its conservative TTL.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import time
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_phase_capacity_reservation import (
    phase_capacity_reservation_repository,
)
from .load_execution_phase_settlement_confirmation import (
    async_observe_phase_settlement_confirmation,
)
from .load_execution_phase_settlement_evidence import (
    phase_settlement_evidence_repository,
)
from .load_execution_phase_settlement_release import (
    async_release_confirmed_phase_reservation,
)

_RUNTIME_KEY = "load_execution_phase_settlement_runtimes_by_entry"
DEFAULT_SETTLEMENT_POLL_SECONDS = 5
SETTLEMENT_STATUS_RETENTION_SECONDS = 3600
MAX_RETAINED_RELEASED_STATUSES = 100

STATUS_WAITING = "waiting"
STATUS_CONFIRMED = "confirmed"
STATUS_RELEASED = "released"
STATUS_ERROR = "error"


@dataclass(frozen=True, slots=True)
class PhaseSettlementRuntimeStatus:
    lifecycle_id: str
    status: str
    last_checked_at: int
    confirmation_status: str | None = None
    release_status: str | None = None
    last_error: str | None = None
    service_call_performed: bool = False
    execution_performed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class PhaseSettlementRuntime:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        if not entry_id:
            raise ValueError("entry_id is required")
        self._hass = hass
        self._entry_id = entry_id
        self._started = False
        self._healthy = True
        self._last_error: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._status_by_lifecycle: dict[str, PhaseSettlementRuntimeStatus] = {}

    @property
    def started(self) -> bool:
        return self._started

    @property
    def healthy(self) -> bool:
        return self._healthy

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def statuses(self) -> tuple[PhaseSettlementRuntimeStatus, ...]:
        return tuple(
            sorted(self._status_by_lifecycle.values(), key=lambda item: item.lifecycle_id)
        )

    async def async_start(self) -> None:
        if self._started:
            return
        self._started = True
        self._task = self._hass.async_create_task(self._run())

    async def async_stop(self) -> None:
        self._started = False
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        try:
            while self._started:
                try:
                    await self.async_process_once()
                    self._healthy = True
                    self._last_error = None
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    self._healthy = False
                    self._last_error = str(err)
                await asyncio.sleep(DEFAULT_SETTLEMENT_POLL_SECONDS)
        except asyncio.CancelledError:
            raise

    def _prune_statuses(self, *, now: int, active_ids: set[str]) -> None:
        """Keep active status plus a bounded recent history of released entries."""
        for lifecycle_id, status in tuple(self._status_by_lifecycle.items()):
            if lifecycle_id in active_ids:
                continue
            if status.status != STATUS_RELEASED:
                self._status_by_lifecycle.pop(lifecycle_id, None)
                continue
            if now - status.last_checked_at > SETTLEMENT_STATUS_RETENTION_SECONDS:
                self._status_by_lifecycle.pop(lifecycle_id, None)

        released = sorted(
            (
                status
                for lifecycle_id, status in self._status_by_lifecycle.items()
                if lifecycle_id not in active_ids and status.status == STATUS_RELEASED
            ),
            key=lambda item: (item.last_checked_at, item.lifecycle_id),
            reverse=True,
        )
        for status in released[MAX_RETAINED_RELEASED_STATUSES:]:
            self._status_by_lifecycle.pop(status.lifecycle_id, None)

    async def async_process_once(self) -> None:
        """Advance settlement for active reservations without any physical action."""
        async with self._lock:
            now = max(1, int(time.time()))
            reservations = await phase_capacity_reservation_repository(
                self._hass, self._entry_id
            ).async_snapshot(now=now)
            active_ids = {item.lifecycle_id for item in reservations}
            self._prune_statuses(now=now, active_ids=active_ids)

            for reservation in reservations:
                lifecycle_id = reservation.lifecycle_id
                try:
                    confirmation = await async_observe_phase_settlement_confirmation(
                        self._hass,
                        entry_id=self._entry_id,
                        lifecycle_id=lifecycle_id,
                    )
                    if not confirmation.confirmed:
                        self._status_by_lifecycle[lifecycle_id] = PhaseSettlementRuntimeStatus(
                            lifecycle_id=lifecycle_id,
                            status=STATUS_WAITING,
                            last_checked_at=now,
                            confirmation_status=confirmation.status,
                        )
                        continue

                    release = await async_release_confirmed_phase_reservation(
                        self._hass,
                        entry_id=self._entry_id,
                        lifecycle_id=lifecycle_id,
                    )
                    self._status_by_lifecycle[lifecycle_id] = PhaseSettlementRuntimeStatus(
                        lifecycle_id=lifecycle_id,
                        status=STATUS_RELEASED if release.released else STATUS_CONFIRMED,
                        last_checked_at=now,
                        confirmation_status=confirmation.status,
                        release_status=release.status,
                    )
                except Exception as err:
                    self._status_by_lifecycle[lifecycle_id] = PhaseSettlementRuntimeStatus(
                        lifecycle_id=lifecycle_id,
                        status=STATUS_ERROR,
                        last_checked_at=now,
                        last_error=str(err),
                    )

            await phase_settlement_evidence_repository(
                self._hass, self._entry_id
            ).async_prune(active_lifecycle_ids=active_ids)


def phase_settlement_runtime(hass: HomeAssistant, entry_id: str) -> PhaseSettlementRuntime:
    if not entry_id:
        raise ValueError("entry_id is required")
    domain_data = hass.data.setdefault(DOMAIN, {})
    runtimes = domain_data.get(_RUNTIME_KEY)
    if not isinstance(runtimes, dict):
        runtimes = {}
        domain_data[_RUNTIME_KEY] = runtimes
    runtime = runtimes.get(entry_id)
    if isinstance(runtime, PhaseSettlementRuntime):
        return runtime
    runtime = PhaseSettlementRuntime(hass, entry_id)
    runtimes[entry_id] = runtime
    return runtime


async def async_start_phase_settlement_runtime(hass: HomeAssistant, entry_id: str) -> None:
    await phase_settlement_runtime(hass, entry_id).async_start()


async def async_stop_phase_settlement_runtime(hass: HomeAssistant, entry_id: str) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    runtimes = domain_data.get(_RUNTIME_KEY)
    if not isinstance(runtimes, dict):
        return
    runtime = runtimes.pop(entry_id, None)
    if isinstance(runtime, PhaseSettlementRuntime):
        await runtime.async_stop()