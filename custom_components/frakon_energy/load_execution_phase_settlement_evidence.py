"""Durable baseline evidence for conservative phase-reservation settlement.

This evidence never authorizes execution and never releases a reservation by itself.
It records the exact L1/L2/L3 snapshot observed immediately before a bounded start so
later code can prove that newer telemetry has absorbed the reserved current.
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

STORAGE_VERSION = 1
SCHEMA_VERSION = 1
_REPOSITORIES_KEY = "load_execution_phase_settlement_evidence_repositories_by_entry"


class PhaseSettlementEvidenceError(RuntimeError):
    """Raised when settlement evidence cannot be trusted."""


class PhaseSettlementEvidenceStore(Protocol):
    async def async_load(self) -> dict[str, Any] | None: ...
    async def async_save(self, data: dict[str, Any]) -> None: ...


def storage_key(entry_id: str) -> str:
    if not entry_id:
        raise PhaseSettlementEvidenceError("entry_id is required")
    digest = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:20]
    return f"{DOMAIN}.load_execution_phase_settlement_evidence.{digest}"


def _non_negative(value: float, field: str) -> float:
    if isinstance(value, bool):
        raise PhaseSettlementEvidenceError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise PhaseSettlementEvidenceError(f"{field} must be finite and non-negative")
    return number


def _timestamp(value: float, field: str) -> float:
    if isinstance(value, bool):
        raise PhaseSettlementEvidenceError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise PhaseSettlementEvidenceError(f"{field} must be finite and positive")
    return number


@dataclass(frozen=True, slots=True)
class PhaseSettlementBaseline:
    lifecycle_id: str
    attempt_id: str
    entity_l1: str
    entity_l2: str
    entity_l3: str
    baseline_l1_a: float
    baseline_l2_a: float
    baseline_l3_a: float
    observed_l1_at: float
    observed_l2_at: float
    observed_l3_at: float
    created_at: int

    def validated(self) -> "PhaseSettlementBaseline":
        if not self.lifecycle_id or not self.attempt_id:
            raise PhaseSettlementEvidenceError("lifecycle_id and attempt_id are required")
        if not self.entity_l1 or not self.entity_l2 or not self.entity_l3:
            raise PhaseSettlementEvidenceError("all phase entity ids are required")
        _non_negative(self.baseline_l1_a, "baseline_l1_a")
        _non_negative(self.baseline_l2_a, "baseline_l2_a")
        _non_negative(self.baseline_l3_a, "baseline_l3_a")
        _timestamp(self.observed_l1_at, "observed_l1_at")
        _timestamp(self.observed_l2_at, "observed_l2_at")
        _timestamp(self.observed_l3_at, "observed_l3_at")
        if self.created_at <= 0:
            raise PhaseSettlementEvidenceError("created_at must be positive")
        return self

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def baselines(self) -> dict[str, float]:
        return {"L1": self.baseline_l1_a, "L2": self.baseline_l2_a, "L3": self.baseline_l3_a}

    def entities(self) -> dict[str, str]:
        return {"L1": self.entity_l1, "L2": self.entity_l2, "L3": self.entity_l3}

    def observed_at(self) -> dict[str, float]:
        return {"L1": self.observed_l1_at, "L2": self.observed_l2_at, "L3": self.observed_l3_at}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PhaseSettlementBaseline":
        try:
            return cls(
                lifecycle_id=str(value["lifecycle_id"]),
                attempt_id=str(value["attempt_id"]),
                entity_l1=str(value["entity_l1"]),
                entity_l2=str(value["entity_l2"]),
                entity_l3=str(value["entity_l3"]),
                baseline_l1_a=float(value["baseline_l1_a"]),
                baseline_l2_a=float(value["baseline_l2_a"]),
                baseline_l3_a=float(value["baseline_l3_a"]),
                observed_l1_at=float(value["observed_l1_at"]),
                observed_l2_at=float(value["observed_l2_at"]),
                observed_l3_at=float(value["observed_l3_at"]),
                created_at=int(value["created_at"]),
            ).validated()
        except (KeyError, TypeError, ValueError) as err:
            if isinstance(err, PhaseSettlementEvidenceError):
                raise
            raise PhaseSettlementEvidenceError("invalid persisted phase settlement evidence") from err


class PhaseSettlementEvidenceRepository:
    def __init__(self, store: PhaseSettlementEvidenceStore) -> None:
        self._store = store
        self._lock = asyncio.Lock()
        self._loaded = False
        self._items: dict[str, PhaseSettlementBaseline] = {}

    async def _async_load(self) -> None:
        if self._loaded:
            return
        raw = await self._store.async_load()
        if raw is None:
            self._loaded = True
            return
        if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
            raise PhaseSettlementEvidenceError("unsupported phase settlement evidence schema")
        values = raw.get("items")
        if not isinstance(values, list):
            raise PhaseSettlementEvidenceError("phase settlement evidence must contain a list")
        loaded: dict[str, PhaseSettlementBaseline] = {}
        for raw_item in values:
            if not isinstance(raw_item, dict):
                raise PhaseSettlementEvidenceError("phase settlement evidence item must be an object")
            item = PhaseSettlementBaseline.from_dict(raw_item)
            if item.lifecycle_id in loaded:
                raise PhaseSettlementEvidenceError("duplicate phase settlement lifecycle_id")
            loaded[item.lifecycle_id] = item
        self._items = loaded
        self._loaded = True

    async def _async_save(self, items: dict[str, PhaseSettlementBaseline]) -> None:
        await self._store.async_save({
            "schema_version": SCHEMA_VERSION,
            "items": [item.as_dict() for item in sorted(items.values(), key=lambda item: item.lifecycle_id)],
        })

    async def async_get(self, lifecycle_id: str) -> PhaseSettlementBaseline | None:
        if not lifecycle_id:
            raise PhaseSettlementEvidenceError("lifecycle_id is required")
        async with self._lock:
            await self._async_load()
            return self._items.get(lifecycle_id)

    async def async_put(self, item: PhaseSettlementBaseline) -> tuple[PhaseSettlementBaseline, bool]:
        candidate = item.validated()
        async with self._lock:
            await self._async_load()
            existing = self._items.get(candidate.lifecycle_id)
            if existing is not None:
                if existing != candidate:
                    raise PhaseSettlementEvidenceError("existing phase settlement evidence binding mismatch")
                return existing, False
            updated = dict(self._items)
            updated[candidate.lifecycle_id] = candidate
            await self._async_save(updated)
            self._items = updated
            return candidate, True


def home_assistant_phase_settlement_evidence_repository(
    hass: HomeAssistant, entry_id: str
) -> PhaseSettlementEvidenceRepository:
    return PhaseSettlementEvidenceRepository(Store(hass, STORAGE_VERSION, storage_key(entry_id)))


def phase_settlement_evidence_repository(
    hass: HomeAssistant, entry_id: str
) -> PhaseSettlementEvidenceRepository:
    if not entry_id:
        raise PhaseSettlementEvidenceError("entry_id is required")
    domain_data = hass.data.setdefault(DOMAIN, {})
    repositories = domain_data.get(_REPOSITORIES_KEY)
    if not isinstance(repositories, dict):
        repositories = {}
        domain_data[_REPOSITORIES_KEY] = repositories
    repository = repositories.get(entry_id)
    if isinstance(repository, PhaseSettlementEvidenceRepository):
        return repository
    repository = home_assistant_phase_settlement_evidence_repository(hass, entry_id)
    repositories[entry_id] = repository
    return repository
