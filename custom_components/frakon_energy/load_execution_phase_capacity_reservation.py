"""Durable short-lived per-phase current reservations for bounded starts.

Reservations cover the telemetry gap between a successful physical start and
L1/L2/L3 current sensors reflecting the new load. They never authorize
execution; they only consume phase headroom for later starts.
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

PHASE_CAPACITY_RESERVATION_STORAGE_VERSION = 1
PHASE_CAPACITY_RESERVATION_SCHEMA_VERSION = 1
DEFAULT_PHASE_CAPACITY_RESERVATION_SECONDS = 300
_REPOSITORIES_KEY = "load_execution_phase_capacity_reservation_repositories_by_entry"


class PhaseCapacityReservationError(RuntimeError):
    """Raised when durable phase-capacity reservation state cannot be trusted."""


class PhaseCapacityReservationStore(Protocol):
    async def async_load(self) -> dict[str, Any] | None: ...
    async def async_save(self, data: dict[str, Any]) -> None: ...


def phase_capacity_reservation_storage_key(entry_id: str) -> str:
    if not entry_id:
        raise PhaseCapacityReservationError("entry_id is required")
    digest = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:20]
    return f"{DOMAIN}.load_execution_phase_capacity_reservations.{digest}"


def _current(value: float, field: str) -> float:
    if isinstance(value, bool):
        raise PhaseCapacityReservationError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise PhaseCapacityReservationError(f"{field} must be finite and non-negative")
    return number


@dataclass(frozen=True, slots=True)
class PhaseCapacityReservation:
    lifecycle_id: str
    attempt_id: str
    current_l1_a: float
    current_l2_a: float
    current_l3_a: float
    created_at: int
    expires_at: int

    def validated(self) -> "PhaseCapacityReservation":
        if not self.lifecycle_id or not self.attempt_id:
            raise PhaseCapacityReservationError("reservation lifecycle_id and attempt_id are required")
        currents = (
            _current(self.current_l1_a, "current_l1_a"),
            _current(self.current_l2_a, "current_l2_a"),
            _current(self.current_l3_a, "current_l3_a"),
        )
        if not any(value > 0 for value in currents):
            raise PhaseCapacityReservationError("reservation must consume at least one phase")
        if self.created_at <= 0 or self.expires_at <= self.created_at:
            raise PhaseCapacityReservationError("reservation timestamps are invalid")
        return self

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def currents(self) -> dict[str, float]:
        return {
            "L1": self.current_l1_a,
            "L2": self.current_l2_a,
            "L3": self.current_l3_a,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PhaseCapacityReservation":
        try:
            return cls(
                lifecycle_id=str(value["lifecycle_id"]),
                attempt_id=str(value["attempt_id"]),
                current_l1_a=float(value["current_l1_a"]),
                current_l2_a=float(value["current_l2_a"]),
                current_l3_a=float(value["current_l3_a"]),
                created_at=int(value["created_at"]),
                expires_at=int(value["expires_at"]),
            ).validated()
        except (KeyError, TypeError, ValueError) as err:
            if isinstance(err, PhaseCapacityReservationError):
                raise
            raise PhaseCapacityReservationError("invalid persisted phase-capacity reservation") from err


class PhaseCapacityReservationRepository:
    """Transactional durable phase reservation set keyed by lifecycle id."""

    def __init__(self, store: PhaseCapacityReservationStore) -> None:
        self._store = store
        self._lock = asyncio.Lock()
        self._loaded = False
        self._reservations: dict[str, PhaseCapacityReservation] = {}

    async def _async_load(self) -> None:
        if self._loaded:
            return
        raw = await self._store.async_load()
        if raw is None:
            self._loaded = True
            return
        if not isinstance(raw, dict) or raw.get("schema_version") != PHASE_CAPACITY_RESERVATION_SCHEMA_VERSION:
            raise PhaseCapacityReservationError("unsupported phase-capacity reservation storage schema")
        items = raw.get("reservations")
        if not isinstance(items, list):
            raise PhaseCapacityReservationError("phase-capacity reservation storage must contain a list")
        loaded: dict[str, PhaseCapacityReservation] = {}
        for value in items:
            if not isinstance(value, dict):
                raise PhaseCapacityReservationError("phase-capacity reservation item must be an object")
            reservation = PhaseCapacityReservation.from_dict(value)
            if reservation.lifecycle_id in loaded:
                raise PhaseCapacityReservationError("duplicate phase-capacity reservation lifecycle_id")
            loaded[reservation.lifecycle_id] = reservation
        self._reservations = loaded
        self._loaded = True

    async def _async_save(self, reservations: dict[str, PhaseCapacityReservation]) -> None:
        await self._store.async_save(
            {
                "schema_version": PHASE_CAPACITY_RESERVATION_SCHEMA_VERSION,
                "reservations": [
                    item.as_dict()
                    for item in sorted(reservations.values(), key=lambda value: value.lifecycle_id)
                ],
            }
        )

    async def async_snapshot(self, *, now: int) -> tuple[PhaseCapacityReservation, ...]:
        """Return active reservations without mutating durable state."""
        if now <= 0:
            raise PhaseCapacityReservationError("now must be positive")
        async with self._lock:
            await self._async_load()
            return tuple(
                sorted(
                    (value for value in self._reservations.values() if value.expires_at > now),
                    key=lambda value: value.lifecycle_id,
                )
            )

    async def async_active(self, *, now: int) -> tuple[PhaseCapacityReservation, ...]:
        """Return active reservations and compact expired durable entries."""
        if now <= 0:
            raise PhaseCapacityReservationError("now must be positive")
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
        current_l1_a: float,
        current_l2_a: float,
        current_l3_a: float,
        now: int,
        ttl_seconds: int = DEFAULT_PHASE_CAPACITY_RESERVATION_SECONDS,
    ) -> tuple[PhaseCapacityReservation, bool]:
        if ttl_seconds <= 0:
            raise PhaseCapacityReservationError("ttl_seconds must be positive")
        candidate = PhaseCapacityReservation(
            lifecycle_id=lifecycle_id,
            attempt_id=attempt_id,
            current_l1_a=current_l1_a,
            current_l2_a=current_l2_a,
            current_l3_a=current_l3_a,
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
                same = (
                    existing.attempt_id == attempt_id
                    and math.isclose(existing.current_l1_a, current_l1_a, rel_tol=1e-9, abs_tol=1e-9)
                    and math.isclose(existing.current_l2_a, current_l2_a, rel_tol=1e-9, abs_tol=1e-9)
                    and math.isclose(existing.current_l3_a, current_l3_a, rel_tol=1e-9, abs_tol=1e-9)
                )
                if not same:
                    raise PhaseCapacityReservationError("existing phase-capacity reservation binding mismatch")
                if active != self._reservations:
                    await self._async_save(active)
                    self._reservations = active
                return existing, False
            updated = dict(active)
            updated[lifecycle_id] = candidate
            await self._async_save(updated)
            self._reservations = updated
            return candidate, True


def home_assistant_phase_capacity_reservation_repository(
    hass: HomeAssistant,
    entry_id: str,
) -> PhaseCapacityReservationRepository:
    return PhaseCapacityReservationRepository(
        Store(
            hass,
            PHASE_CAPACITY_RESERVATION_STORAGE_VERSION,
            phase_capacity_reservation_storage_key(entry_id),
        )
    )


def phase_capacity_reservation_repository(
    hass: HomeAssistant,
    entry_id: str,
) -> PhaseCapacityReservationRepository:
    if not entry_id:
        raise PhaseCapacityReservationError("entry_id is required")
    domain_data = hass.data.setdefault(DOMAIN, {})
    repositories = domain_data.get(_REPOSITORIES_KEY)
    if not isinstance(repositories, dict):
        repositories = {}
        domain_data[_REPOSITORIES_KEY] = repositories
    repository = repositories.get(entry_id)
    if isinstance(repository, PhaseCapacityReservationRepository):
        return repository
    repository = home_assistant_phase_capacity_reservation_repository(hass, entry_id)
    repositories[entry_id] = repository
    return repository
