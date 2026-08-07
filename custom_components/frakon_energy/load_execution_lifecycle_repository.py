"""Persistent repository for immutable execution lifecycle records.

This module persists lifecycle records only. It deliberately has no execution-flow
integration, WebSocket API, Home Assistant service call, or executor.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Protocol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .load_execution_lifecycle_record import ExecutionLifecycleRecord

LIFECYCLE_STORAGE_VERSION = 1
LIFECYCLE_STORAGE_SCHEMA_VERSION = 1


class LifecycleConflictError(ValueError):
    """Raised when one lifecycle identity is reused with different contents."""


class LifecycleStore(Protocol):
    async def async_load(self) -> dict[str, Any] | None: ...
    async def async_save(self, data: dict[str, Any]) -> None: ...


def lifecycle_storage_key(entry_id: str) -> str:
    """Return a storage namespace isolated to one FRAKON Energy config entry."""
    if not entry_id:
        raise ValueError("entry_id is required")
    digest = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:20]
    return f"{DOMAIN}.load_execution_lifecycles.{digest}"


def _record_as_dict(record: ExecutionLifecycleRecord) -> dict[str, Any]:
    validated = record.validated()
    return {
        "lifecycle_id": validated.lifecycle_id,
        "attempt_id": validated.attempt_id,
        "action_snapshot_id": validated.action_snapshot_id,
        "status": validated.status,
        "created_at": validated.created_at,
        "updated_at": validated.updated_at,
        "revision": validated.revision,
    }


def _record_from_dict(value: dict[str, Any]) -> ExecutionLifecycleRecord:
    return ExecutionLifecycleRecord(
        lifecycle_id=str(value.get("lifecycle_id", "")),
        attempt_id=str(value.get("attempt_id", "")),
        action_snapshot_id=str(value.get("action_snapshot_id", "")),
        status=str(value.get("status", "")),
        created_at=int(value.get("created_at", -1)),
        updated_at=int(value.get("updated_at", -1)),
        revision=int(value.get("revision", -1)),
    ).validated()


class ExecutionLifecycleLedger:
    """Validated in-memory view of persisted lifecycle records."""

    def __init__(self, records: tuple[ExecutionLifecycleRecord, ...] = ()) -> None:
        self._by_lifecycle_id: dict[str, ExecutionLifecycleRecord] = {}
        self._by_attempt_id: dict[str, ExecutionLifecycleRecord] = {}
        for record in records:
            validated = record.validated()
            if validated.lifecycle_id in self._by_lifecycle_id:
                raise ValueError(
                    f"duplicate lifecycle_id in lifecycle ledger: {validated.lifecycle_id}"
                )
            if validated.attempt_id in self._by_attempt_id:
                raise ValueError(
                    f"duplicate attempt_id in lifecycle ledger: {validated.attempt_id}"
                )
            self._by_lifecycle_id[validated.lifecycle_id] = validated
            self._by_attempt_id[validated.attempt_id] = validated

    @property
    def records(self) -> tuple[ExecutionLifecycleRecord, ...]:
        return tuple(
            sorted(
                self._by_lifecycle_id.values(),
                key=lambda item: (item.created_at, item.lifecycle_id),
            )
        )

    def put(self, record: ExecutionLifecycleRecord) -> tuple[ExecutionLifecycleRecord, bool]:
        """Add one record or accept an exact idempotent replay."""
        candidate = record.validated()
        existing = self._by_lifecycle_id.get(candidate.lifecycle_id)
        if existing is not None:
            if existing == candidate:
                return existing, False
            raise LifecycleConflictError(
                "lifecycle_id already exists with different record contents"
            )

        by_attempt = self._by_attempt_id.get(candidate.attempt_id)
        if by_attempt is not None:
            raise LifecycleConflictError(
                "attempt_id already has a different lifecycle record"
            )

        self._by_lifecycle_id[candidate.lifecycle_id] = candidate
        self._by_attempt_id[candidate.attempt_id] = candidate
        return candidate, True

    def get_by_lifecycle_id(self, lifecycle_id: str) -> ExecutionLifecycleRecord | None:
        return self._by_lifecycle_id.get(lifecycle_id)

    def get_by_attempt_id(self, attempt_id: str) -> ExecutionLifecycleRecord | None:
        return self._by_attempt_id.get(attempt_id)

    def as_storage(self) -> dict[str, Any]:
        return {
            "schema_version": LIFECYCLE_STORAGE_SCHEMA_VERSION,
            "records": [_record_as_dict(record) for record in self.records],
        }

    @classmethod
    def from_storage(cls, value: dict[str, Any] | None) -> "ExecutionLifecycleLedger":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("lifecycle storage must be an object")
        if value.get("schema_version") != LIFECYCLE_STORAGE_SCHEMA_VERSION:
            raise ValueError("unsupported execution lifecycle storage schema")
        raw_records = value.get("records", [])
        if not isinstance(raw_records, list):
            raise ValueError("lifecycle records must be a list")

        records: list[ExecutionLifecycleRecord] = []
        for raw in raw_records:
            if not isinstance(raw, dict):
                raise ValueError("lifecycle record must be an object")
            records.append(_record_from_dict(raw))
        return cls(tuple(records))


class ExecutionLifecycleRepository:
    """Async persistent repository with rollback on save failure."""

    def __init__(self, store: LifecycleStore) -> None:
        self._store = store
        self._lock = asyncio.Lock()
        self._ledger: ExecutionLifecycleLedger | None = None

    async def _async_ledger(self) -> ExecutionLifecycleLedger:
        if self._ledger is None:
            self._ledger = ExecutionLifecycleLedger.from_storage(
                await self._store.async_load()
            )
        return self._ledger

    async def async_put(
        self, record: ExecutionLifecycleRecord
    ) -> tuple[ExecutionLifecycleRecord, bool]:
        """Persist one record, returning whether a new row was created."""
        async with self._lock:
            current = await self._async_ledger()
            candidate = ExecutionLifecycleLedger(current.records)
            stored, created = candidate.put(record)
            if created:
                await self._store.async_save(candidate.as_storage())
                self._ledger = candidate
            return stored, created

    async def async_list(self) -> tuple[ExecutionLifecycleRecord, ...]:
        async with self._lock:
            return (await self._async_ledger()).records

    async def async_get_by_lifecycle_id(
        self, lifecycle_id: str
    ) -> ExecutionLifecycleRecord | None:
        async with self._lock:
            return (await self._async_ledger()).get_by_lifecycle_id(lifecycle_id)

    async def async_get_by_attempt_id(
        self, attempt_id: str
    ) -> ExecutionLifecycleRecord | None:
        async with self._lock:
            return (await self._async_ledger()).get_by_attempt_id(attempt_id)


def home_assistant_lifecycle_repository(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionLifecycleRepository:
    """Create a lifecycle repository backed by Home Assistant storage."""
    store = Store(hass, LIFECYCLE_STORAGE_VERSION, lifecycle_storage_key(entry_id))
    return ExecutionLifecycleRepository(store)
