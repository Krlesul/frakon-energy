"""Durable two-sample confirmation for phase reservation settlement.

This layer still never releases a reservation. It requires an already verified
execution lifecycle plus two independent positive phase-settlement proofs separated
by a minimum telemetry interval before marking settlement as confirmed.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, replace
import hashlib
import math
import time
from typing import Any, Protocol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .load_execution_lifecycle import STATE_VERIFIED
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_execution_phase_settlement_proof import (
    PhaseSettlementProof,
    async_phase_settlement_proof,
)

STORAGE_VERSION = 1
SCHEMA_VERSION = 1
MIN_CONFIRMATION_INTERVAL_SECONDS = 5.0
_REPOSITORIES_KEY = "load_execution_phase_settlement_confirmation_repositories_by_entry"

STATUS_LIFECYCLE_NOT_FOUND = "lifecycle_not_found"
STATUS_LIFECYCLE_NOT_VERIFIED = "lifecycle_not_verified"
STATUS_PROOF_NOT_READY = "proof_not_ready"
STATUS_FIRST_OBSERVATION = "first_observation_recorded"
STATUS_WAITING_FOR_NEW_SAMPLE = "waiting_for_new_sample"
STATUS_CONFIRMED = "confirmed"


class PhaseSettlementConfirmationError(RuntimeError):
    """Raised when confirmation state cannot be trusted."""


class ConfirmationStore(Protocol):
    async def async_load(self) -> dict[str, Any] | None: ...
    async def async_save(self, data: dict[str, Any]) -> None: ...


def storage_key(entry_id: str) -> str:
    if not entry_id:
        raise PhaseSettlementConfirmationError("entry_id is required")
    digest = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:20]
    return f"{DOMAIN}.load_execution_phase_settlement_confirmation.{digest}"


def _finite_positive(value: float, field: str) -> float:
    if isinstance(value, bool):
        raise PhaseSettlementConfirmationError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise PhaseSettlementConfirmationError(f"{field} must be finite and positive")
    return number


@dataclass(frozen=True, slots=True)
class PhaseSettlementObservation:
    lifecycle_id: str
    attempt_id: str
    first_watermark: float
    first_source_updated_at: dict[str, float]
    first_current_a: dict[str, float]
    confirmed_at: int | None = None
    confirmed_watermark: float | None = None

    def validated(self) -> "PhaseSettlementObservation":
        if not self.lifecycle_id or not self.attempt_id:
            raise PhaseSettlementConfirmationError("lifecycle_id and attempt_id are required")
        _finite_positive(self.first_watermark, "first_watermark")
        for phase in ("L1", "L2", "L3"):
            _finite_positive(self.first_source_updated_at[phase], f"first_source_updated_at.{phase}")
            current = float(self.first_current_a[phase])
            if not math.isfinite(current) or current < 0:
                raise PhaseSettlementConfirmationError(f"first_current_a.{phase} must be finite and non-negative")
        if self.confirmed_at is not None and self.confirmed_at <= 0:
            raise PhaseSettlementConfirmationError("confirmed_at must be positive")
        if self.confirmed_watermark is not None:
            _finite_positive(self.confirmed_watermark, "confirmed_watermark")
            if self.confirmed_watermark < self.first_watermark + MIN_CONFIRMATION_INTERVAL_SECONDS:
                raise PhaseSettlementConfirmationError("confirmed watermark is too close to first observation")
        if (self.confirmed_at is None) != (self.confirmed_watermark is None):
            raise PhaseSettlementConfirmationError("confirmation timestamps must be both set or both unset")
        return self

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PhaseSettlementObservation":
        try:
            raw_updates = value["first_source_updated_at"]
            raw_currents = value["first_current_a"]
            if not isinstance(raw_updates, dict) or not isinstance(raw_currents, dict):
                raise PhaseSettlementConfirmationError("confirmation phase maps are invalid")
            return cls(
                lifecycle_id=str(value["lifecycle_id"]),
                attempt_id=str(value["attempt_id"]),
                first_watermark=float(value["first_watermark"]),
                first_source_updated_at={phase: float(raw_updates[phase]) for phase in ("L1", "L2", "L3")},
                first_current_a={phase: float(raw_currents[phase]) for phase in ("L1", "L2", "L3")},
                confirmed_at=int(value["confirmed_at"]) if value.get("confirmed_at") is not None else None,
                confirmed_watermark=float(value["confirmed_watermark"]) if value.get("confirmed_watermark") is not None else None,
            ).validated()
        except (KeyError, TypeError, ValueError) as err:
            if isinstance(err, PhaseSettlementConfirmationError):
                raise
            raise PhaseSettlementConfirmationError("invalid persisted settlement confirmation") from err


class PhaseSettlementConfirmationRepository:
    def __init__(self, store: ConfirmationStore) -> None:
        self._store = store
        self._lock = asyncio.Lock()
        self._loaded = False
        self._items: dict[str, PhaseSettlementObservation] = {}

    async def _async_load(self) -> None:
        if self._loaded:
            return
        raw = await self._store.async_load()
        if raw is None:
            self._loaded = True
            return
        if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
            raise PhaseSettlementConfirmationError("unsupported settlement confirmation schema")
        raw_items = raw.get("items")
        if not isinstance(raw_items, list):
            raise PhaseSettlementConfirmationError("settlement confirmation must contain a list")
        loaded: dict[str, PhaseSettlementObservation] = {}
        for value in raw_items:
            if not isinstance(value, dict):
                raise PhaseSettlementConfirmationError("settlement confirmation item must be an object")
            item = PhaseSettlementObservation.from_dict(value)
            if item.lifecycle_id in loaded:
                raise PhaseSettlementConfirmationError("duplicate settlement confirmation lifecycle_id")
            loaded[item.lifecycle_id] = item
        self._items = loaded
        self._loaded = True

    async def _async_save(self, items: dict[str, PhaseSettlementObservation]) -> None:
        await self._store.async_save({
            "schema_version": SCHEMA_VERSION,
            "items": [item.as_dict() for item in sorted(items.values(), key=lambda item: item.lifecycle_id)],
        })

    async def async_get(self, lifecycle_id: str) -> PhaseSettlementObservation | None:
        if not lifecycle_id:
            raise PhaseSettlementConfirmationError("lifecycle_id is required")
        async with self._lock:
            await self._async_load()
            return self._items.get(lifecycle_id)

    async def async_record_first(
        self,
        *,
        lifecycle_id: str,
        attempt_id: str,
        watermark: float,
        source_updated_at: dict[str, float],
        current_a: dict[str, float],
    ) -> tuple[PhaseSettlementObservation, bool]:
        candidate = PhaseSettlementObservation(
            lifecycle_id=lifecycle_id,
            attempt_id=attempt_id,
            first_watermark=watermark,
            first_source_updated_at=dict(source_updated_at),
            first_current_a=dict(current_a),
        ).validated()
        async with self._lock:
            await self._async_load()
            existing = self._items.get(lifecycle_id)
            if existing is not None:
                if existing.attempt_id != attempt_id:
                    raise PhaseSettlementConfirmationError("existing confirmation attempt binding mismatch")
                return existing, False
            updated = dict(self._items)
            updated[lifecycle_id] = candidate
            await self._async_save(updated)
            self._items = updated
            return candidate, True

    async def async_confirm(
        self,
        *,
        lifecycle_id: str,
        attempt_id: str,
        watermark: float,
        confirmed_at: int,
    ) -> tuple[PhaseSettlementObservation, bool]:
        async with self._lock:
            await self._async_load()
            existing = self._items.get(lifecycle_id)
            if existing is None:
                raise PhaseSettlementConfirmationError("first settlement observation is missing")
            if existing.attempt_id != attempt_id:
                raise PhaseSettlementConfirmationError("existing confirmation attempt binding mismatch")
            if existing.confirmed_at is not None:
                return existing, False
            if watermark < existing.first_watermark + MIN_CONFIRMATION_INTERVAL_SECONDS:
                raise PhaseSettlementConfirmationError("second settlement observation is too close to first")
            candidate = replace(
                existing,
                confirmed_at=confirmed_at,
                confirmed_watermark=watermark,
            ).validated()
            updated = dict(self._items)
            updated[lifecycle_id] = candidate
            await self._async_save(updated)
            self._items = updated
            return candidate, True


def home_assistant_confirmation_repository(
    hass: HomeAssistant, entry_id: str
) -> PhaseSettlementConfirmationRepository:
    return PhaseSettlementConfirmationRepository(Store(hass, STORAGE_VERSION, storage_key(entry_id)))


def phase_settlement_confirmation_repository(
    hass: HomeAssistant, entry_id: str
) -> PhaseSettlementConfirmationRepository:
    if not entry_id:
        raise PhaseSettlementConfirmationError("entry_id is required")
    domain_data = hass.data.setdefault(DOMAIN, {})
    repositories = domain_data.get(_REPOSITORIES_KEY)
    if not isinstance(repositories, dict):
        repositories = {}
        domain_data[_REPOSITORIES_KEY] = repositories
    repository = repositories.get(entry_id)
    if isinstance(repository, PhaseSettlementConfirmationRepository):
        return repository
    repository = home_assistant_confirmation_repository(hass, entry_id)
    repositories[entry_id] = repository
    return repository


@dataclass(frozen=True, slots=True)
class PhaseSettlementConfirmationResult:
    lifecycle_id: str
    status: str
    confirmed: bool
    reason: str
    proof: dict[str, Any] | None
    observation: dict[str, Any] | None
    reservation_release_performed: bool = False
    service_call_performed: bool = False
    execution_performed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _result(
    lifecycle_id: str,
    *,
    status: str,
    reason: str,
    proof: PhaseSettlementProof | None = None,
    observation: PhaseSettlementObservation | None = None,
) -> PhaseSettlementConfirmationResult:
    return PhaseSettlementConfirmationResult(
        lifecycle_id=lifecycle_id,
        status=status,
        confirmed=bool(observation and observation.confirmed_at is not None),
        reason=reason,
        proof=proof.as_dict() if proof is not None else None,
        observation=observation.as_dict() if observation is not None else None,
    )


async def async_observe_phase_settlement_confirmation(
    hass: HomeAssistant,
    *,
    entry_id: str,
    lifecycle_id: str,
) -> PhaseSettlementConfirmationResult:
    """Record/confirm settlement only after verified lifecycle and two positive samples."""
    if not entry_id or not lifecycle_id:
        raise ValueError("entry_id and lifecycle_id are required")

    records = await lifecycle_repository(hass, entry_id).async_list()
    lifecycle = next((record for record in records if record.lifecycle_id == lifecycle_id), None)
    if lifecycle is None:
        return _result(lifecycle_id, status=STATUS_LIFECYCLE_NOT_FOUND, reason="Execution lifecycle was not found.")
    if lifecycle.state != STATE_VERIFIED:
        return _result(
            lifecycle_id,
            status=STATUS_LIFECYCLE_NOT_VERIFIED,
            reason="Execution lifecycle is not durably verified yet.",
        )

    proof = await async_phase_settlement_proof(hass, entry_id=entry_id, lifecycle_id=lifecycle_id)
    if not proof.candidate or proof.reservation is None:
        return _result(
            lifecycle_id,
            status=STATUS_PROOF_NOT_READY,
            reason=proof.reason,
            proof=proof,
        )

    reservation = proof.reservation
    attempt_id = str(reservation["attempt_id"])
    reserved = {
        "L1": float(reservation["current_l1_a"]),
        "L2": float(reservation["current_l2_a"]),
        "L3": float(reservation["current_l3_a"]),
    }
    affected = [phase for phase in ("L1", "L2", "L3") if reserved[phase] > 0]
    if not affected:
        raise PhaseSettlementConfirmationError("settlement proof has no affected phases")
    source_updates = {
        phase: float(proof.source_updated_at[phase])
        for phase in ("L1", "L2", "L3")
        if proof.source_updated_at[phase] is not None
    }
    currents = {
        phase: float(proof.current_a[phase])
        for phase in ("L1", "L2", "L3")
        if proof.current_a[phase] is not None
    }
    if len(source_updates) != 3 or len(currents) != 3:
        raise PhaseSettlementConfirmationError("candidate proof is missing phase telemetry")
    watermark = min(source_updates[phase] for phase in affected)

    repository = phase_settlement_confirmation_repository(hass, entry_id)
    existing = await repository.async_get(lifecycle_id)
    if existing is None:
        observation, _ = await repository.async_record_first(
            lifecycle_id=lifecycle_id,
            attempt_id=attempt_id,
            watermark=watermark,
            source_updated_at=source_updates,
            current_a=currents,
        )
        return _result(
            lifecycle_id,
            status=STATUS_FIRST_OBSERVATION,
            reason="First positive settlement observation recorded; a later independent sample is required.",
            proof=proof,
            observation=observation,
        )

    if existing.confirmed_at is not None:
        return _result(
            lifecycle_id,
            status=STATUS_CONFIRMED,
            reason="Settlement was already durably confirmed.",
            proof=proof,
            observation=existing,
        )

    if watermark < existing.first_watermark + MIN_CONFIRMATION_INTERVAL_SECONDS:
        return _result(
            lifecycle_id,
            status=STATUS_WAITING_FOR_NEW_SAMPLE,
            reason="Positive telemetry has not advanced far enough beyond the first settlement observation.",
            proof=proof,
            observation=existing,
        )
    for phase in affected:
        if source_updates[phase] <= existing.first_source_updated_at[phase]:
            return _result(
                lifecycle_id,
                status=STATUS_WAITING_FOR_NEW_SAMPLE,
                reason=f"Affected phase {phase} does not yet have a second independent sample.",
                proof=proof,
                observation=existing,
            )

    observation, _ = await repository.async_confirm(
        lifecycle_id=lifecycle_id,
        attempt_id=attempt_id,
        watermark=watermark,
        confirmed_at=int(time.time()),
    )
    return _result(
        lifecycle_id,
        status=STATUS_CONFIRMED,
        reason="Verified lifecycle and two independent phase-current samples confirm settlement.",
        proof=proof,
        observation=observation,
    )
