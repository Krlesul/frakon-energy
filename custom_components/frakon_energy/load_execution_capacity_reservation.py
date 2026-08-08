"""Durable short-lived grid-capacity reservations for bounded starts.

Reservations cover the telemetry gap between a successful physical start and the
whole-site grid meter reflecting that new load. They never authorize execution;
they only subtract capacity from later starts. Expired reservations are ignored
and compacted opportunistically.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import hashlib
import math
from typing import Any, Protocol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

CAPACITY_RESERVATION_STORAGE_VERSION = 1
CAPACITY_RESERVATION_SCHEMA_VERSION = 1
DEFAULT_CAPACITY_RESERVATION_SECONDS = 300
_REPOSITORIES_KEY = "load_execution_capacity_reservation_repositories_by_entry"


class CapacityReservationError(RuntimeError):
    """Raised when durable capacity reservation state cannot be trusted."""


class CapacityReservationStore(Protocol):
    async def async_load(self) -> dict[str, Any] | None: ...
    async def async_save(self, data: dict[str, Any]) -> None: ...


def capacity_reservation_storage_key(entry_id: str) -> str:
    if not entry_id:
        raise CapacityReservationError("entry_id is required")
    digest = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:20]
    return f"{DOMAIN}.load_execution_capacity_reservations.{digest}"


@dataclass(frozen=True, slots=True)
class CapacityReservation:
    lifecycle_id: str
    attempt_id: str
    power_kw: float
    created_at: int
    expires_at: int

    def validated(self) -> "CapacityReservation":
        if not self.lifecycle_id or not self.attempt_id:
            raise CapacityReservationError("reservation lifecycle_id and attempt_id are required")
        if isinstance(self.power_kw, bool) or not math.isfinite(self.power_kw) or self.power_kw <= 0:
            raise CapacityReservationError("reservation power_kw must be finite and positive")
        if self.created_at <= 0 or self.expires_at <= self.created_at:
            raise CapacityReservationError("reservation timestamps are invalid")
        return self

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CapacityReservation":
        try:
            return cls(
                lifecycle_id=str(value["lifecycle_id"]),
                attempt_id=str(value["attempt_id"]),
                power_kw=float(value["power_kw"]),
                created_at=int(value["created_at"]),
                expires_at=int(value["expires_at"]),
            ).validated()
        except (KeyError, TypeError, ValueError) as err:
            if isinstance(err, CapacityReservationError):
                raise
            raise CapacityReservationError("invalid persisted capacity reservation") from err


class CapacityReservationRepository:
    """Transactional durable reservation set keyed by lifecycle id."""

    def __init__(self, store: CapacityReservationStore) -> None:
        self._store = store
        self._lock = asyncio.Lock()
        self._loaded = False
        self._reservations: dict[str, CapacityReservation] = {}

    async def _async_load(self) -> None:
        if self._loaded:
            return
        raw = await self._store.async_load()
        if raw is None:
            self._loaded = True
            return
        if not isinstance(raw, dict) or raw.get("schema_version") != CAPACITY_RESERVATION_SCHEMA_VERSION:
            raise CapacityReservationError("unsupported capacity reservation storage schema")
        items = raw.get("reservations")
        if not isinstance(items, list):
            raise CapacityReservationError("capacity reservation storage must contain a list")
        loaded: dict[str, CapacityReservation] = {}
        for value in items:
            if not isinstance(value, dict):
                raise CapacityReservationError("capacity reservation item must be an object")
            reservation = CapacityReservation.from_dict(value)
            if reservation.lifecycle_id in loaded:
                raise CapacityReservationError("duplicate capacity reservation lifecycle_id")
            loaded[reservation.lifecycle_id] = reservation
        self._reservations = loaded
        self._loaded = True

    async def _async_save(self, reservations: dict[str, CapacityReservation]) -> None:
        await self._store.async_save(
            {
                "schema_version": CAPACITY_RESERVATION_SCHEMA_VERSION,
                "reservations": [
                    item.as_dict()
                    for item in sorted(reservations.values(), key=lambda value: value.lifecycle_id)
                ],
            }
        )

    async def async_active(self, *, now: int) -> tuple[CapacityReservation, ...]:
        if now <= 0:
            raise CapacityReservationError("now must be positive")
        async with self._lock:
            await self._async_load()
            active = {
                key: value for key, value in self._reservations.items() if value.expires_at > now
            }
            if active != self._reservations:
                await self._async_save(active)
                self._reservations = active
            return tuple(sorted(active.values(), key=lambda value: value.lifecycle_id))

    async def async_reserve(
        self,
        *,
        lifecycle_id: str,
        attempt_id: str,
        power_kw: float,
        now: int,
        ttl_seconds: int = DEFAULT_CAPACITY_RESERVATION_SECONDS,
    ) -> tuple[CapacityReservation, bool]:
        if ttl_seconds <= 0:
            raise CapacityReservationError("ttl_seconds must be positive")
        candidate = CapacityReservation(
            lifecycle_id=lifecycle_id,
            attempt_id=attempt_id,
            power_kw=power_kw,
            created_at=now,
            expires_at=now + ttl_seconds,
        ).validated()
        async with self._lock:
            await self._async_load()
            active = {
                key: value for key, value in self._reservations.items() if value.expires_at > now
            }
            existing = active.get(lifecycle_id)
            if existing is not None:
                if existing.attempt_id != attempt_id or not math.isclose(
                    existing.power_kw, power_kw, rel_tol=1e-9, abs_tol=1e-9
                ):
                    raise CapacityReservationError("existing capacity reservation binding mismatch")
                if active != self._reservations:
                    await self._async_save(active)
                    self._reservations = active
                return existing, False
            updated = dict(active)
            updated[lifecycle_id] = candidate
            await self._async_save(updated)
            self._reservations = updated
            return candidate, True


def home_assistant_capacity_reservation_repository(
    hass: HomeAssistant,
    entry_id: str,
) -> CapacityReservationRepository:
    return CapacityReservationRepository(
        Store(
            hass,
            CAPACITY_RESERVATION_STORAGE_VERSION,
            capacity_reservation_storage_key(entry_id),
        )
    )


def capacity_reservation_repository(
    hass: HomeAssistant,
    entry_id: str,
) -> CapacityReservationRepository:
    if not entry_id:
        raise CapacityReservationError("entry_id is required")
    domain_data = hass.data.setdefault(DOMAIN, {})
    repositories = domain_data.get(_REPOSITORIES_KEY)
    if not isinstance(repositories, dict):
        repositories = {}
        domain_data[_REPOSITORIES_KEY] = repositories
    repository = repositories.get(entry_id)
    if isinstance(repository, CapacityReservationRepository):
        return repository
    repository = home_assistant_capacity_reservation_repository(hass, entry_id)
    repositories[entry_id] = repository
    return repository
