"""Persistent idempotent execution-attempt audit ledger for FRAKON Energy.

This module records consumed approval intent only. It deliberately has no Home
Assistant service mapping or executor and cannot represent a performed action.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any, Protocol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .load_execution_approval import ExecutionApproval

ATTEMPT_STORAGE_VERSION = 1
ATTEMPT_STORAGE_SCHEMA_VERSION = 1
ATTEMPT_STATUS_APPROVAL_CONSUMED = "approval_consumed"
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class AttemptConflictError(ValueError):
    """Raised when an approval ID is reused with different artifact contents."""


class AttemptStore(Protocol):
    async def async_load(self) -> dict[str, Any] | None: ...
    async def async_save(self, data: dict[str, Any]) -> None: ...


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def approval_artifact_fingerprint(approval: ExecutionApproval) -> str:
    """Fingerprint the complete signed approval artifact for retry comparison."""
    return hashlib.sha256(_canonical_json(approval.as_dict())).hexdigest()


def attempt_storage_key(entry_id: str) -> str:
    """Return a storage namespace isolated to one FRAKON Energy config entry."""
    if not entry_id:
        raise ValueError("entry_id is required")
    digest = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:20]
    return f"{DOMAIN}.load_execution_attempts.{digest}"


@dataclass(frozen=True, slots=True)
class ExecutionAttempt:
    """Audit record created after one approval has been consumed."""

    attempt_id: str
    entry_id: str
    profile_id: str
    entity_id: str | None
    approval_id: str
    approval_fingerprint: str
    snapshot_digest: str
    intent: str
    approval_issued_at: int
    approval_expires_at: int
    created_at: int
    status: str = ATTEMPT_STATUS_APPROVAL_CONSUMED
    execution_performed: bool = False
    executor_available: bool = False

    def validated(self) -> "ExecutionAttempt":
        if not self.attempt_id:
            raise ValueError("attempt_id is required")
        if not self.entry_id:
            raise ValueError("entry_id is required")
        if not self.profile_id:
            raise ValueError("profile_id is required")
        if not self.approval_id:
            raise ValueError("approval_id is required")
        if not _HEX_64.fullmatch(self.approval_fingerprint):
            raise ValueError("approval_fingerprint must be a SHA-256 hex digest")
        if not _HEX_64.fullmatch(self.snapshot_digest):
            raise ValueError("snapshot_digest must be a SHA-256 hex digest")
        if not self.intent:
            raise ValueError("intent is required")
        if self.approval_issued_at < 0 or self.approval_expires_at <= self.approval_issued_at:
            raise ValueError("approval timestamps are invalid")
        if not (self.approval_issued_at <= self.created_at < self.approval_expires_at):
            raise ValueError("created_at must be within the approval validity window")
        if self.status != ATTEMPT_STATUS_APPROVAL_CONSUMED:
            raise ValueError(f"unsupported attempt status: {self.status}")
        if self.execution_performed:
            raise ValueError("attempt audit model cannot represent performed execution")
        if self.executor_available:
            raise ValueError("attempt audit model cannot represent an available executor")
        return self

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExecutionAttempt":
        return cls(
            attempt_id=str(value.get("attempt_id", "")),
            entry_id=str(value.get("entry_id", "")),
            profile_id=str(value.get("profile_id", "")),
            entity_id=str(value["entity_id"]) if value.get("entity_id") is not None else None,
            approval_id=str(value.get("approval_id", "")),
            approval_fingerprint=str(value.get("approval_fingerprint", "")),
            snapshot_digest=str(value.get("snapshot_digest", "")),
            intent=str(value.get("intent", "")),
            approval_issued_at=int(value.get("approval_issued_at", -1)),
            approval_expires_at=int(value.get("approval_expires_at", -1)),
            created_at=int(value.get("created_at", -1)),
            status=str(value.get("status", "")),
            execution_performed=bool(value.get("execution_performed", False)),
            executor_available=bool(value.get("executor_available", False)),
        ).validated()

    @classmethod
    def from_consumed_approval(
        cls,
        *,
        entry_id: str,
        profile_id: str,
        entity_id: str | None,
        approval: ExecutionApproval,
        created_at: int,
    ) -> "ExecutionAttempt":
        """Create a deterministic attempt identity for one consumed approval."""
        if not entry_id or not profile_id:
            raise ValueError("entry_id and profile_id are required")
        attempt_id = hashlib.sha256(
            f"{entry_id}\0{approval.approval_id}".encode("utf-8")
        ).hexdigest()[:32]
        return cls(
            attempt_id=attempt_id,
            entry_id=entry_id,
            profile_id=profile_id,
            entity_id=entity_id,
            approval_id=approval.approval_id,
            approval_fingerprint=approval_artifact_fingerprint(approval),
            snapshot_digest=approval.snapshot_digest,
            intent=approval.intent,
            approval_issued_at=approval.issued_at,
            approval_expires_at=approval.expires_at,
            created_at=created_at,
        ).validated()


@dataclass(frozen=True, slots=True)
class AttemptRecordResult:
    attempt: ExecutionAttempt
    created: bool
    idempotent_replay: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt.as_dict(),
            "created": self.created,
            "idempotent_replay": self.idempotent_replay,
            "execution_performed": False,
            "executor_available": False,
        }


class ExecutionAttemptLedger:
    """In-memory validated view of persisted execution-attempt audit records."""

    def __init__(self, attempts: tuple[ExecutionAttempt, ...] = ()) -> None:
        self._by_approval_id: dict[str, ExecutionAttempt] = {}
        self._by_attempt_id: dict[str, ExecutionAttempt] = {}
        for attempt in attempts:
            validated = attempt.validated()
            if validated.approval_id in self._by_approval_id:
                raise ValueError(f"duplicate approval_id in attempt ledger: {validated.approval_id}")
            if validated.attempt_id in self._by_attempt_id:
                raise ValueError(f"duplicate attempt_id in attempt ledger: {validated.attempt_id}")
            self._by_approval_id[validated.approval_id] = validated
            self._by_attempt_id[validated.attempt_id] = validated

    @property
    def attempts(self) -> tuple[ExecutionAttempt, ...]:
        return tuple(sorted(self._by_attempt_id.values(), key=lambda item: (item.created_at, item.attempt_id)))

    def record(self, attempt: ExecutionAttempt) -> AttemptRecordResult:
        candidate = attempt.validated()
        existing = self._by_approval_id.get(candidate.approval_id)
        if existing is not None:
            if (
                existing.approval_fingerprint == candidate.approval_fingerprint
                and existing.snapshot_digest == candidate.snapshot_digest
                and existing.entry_id == candidate.entry_id
                and existing.profile_id == candidate.profile_id
                and existing.entity_id == candidate.entity_id
                and existing.intent == candidate.intent
            ):
                return AttemptRecordResult(existing, created=False, idempotent_replay=True)
            raise AttemptConflictError(
                "approval_id already has an attempt with different artifact or scope"
            )
        if candidate.attempt_id in self._by_attempt_id:
            raise AttemptConflictError("attempt_id already exists with a different approval")
        self._by_approval_id[candidate.approval_id] = candidate
        self._by_attempt_id[candidate.attempt_id] = candidate
        return AttemptRecordResult(candidate, created=True, idempotent_replay=False)

    def get_by_approval_id(self, approval_id: str) -> ExecutionAttempt | None:
        return self._by_approval_id.get(approval_id)

    def as_storage(self) -> dict[str, Any]:
        return {
            "schema_version": ATTEMPT_STORAGE_SCHEMA_VERSION,
            "attempts": [attempt.as_dict() for attempt in self.attempts],
        }

    @classmethod
    def from_storage(cls, value: dict[str, Any] | None) -> "ExecutionAttemptLedger":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("attempt storage must be an object")
        if value.get("schema_version") != ATTEMPT_STORAGE_SCHEMA_VERSION:
            raise ValueError("unsupported execution attempt storage schema")
        raw_attempts = value.get("attempts", [])
        if not isinstance(raw_attempts, list):
            raise ValueError("attempts must be a list")
        attempts: list[ExecutionAttempt] = []
        for raw in raw_attempts:
            if not isinstance(raw, dict):
                raise ValueError("attempt record must be an object")
            attempts.append(ExecutionAttempt.from_dict(raw))
        return cls(tuple(attempts))


class ExecutionAttemptRepository:
    """Async persistent repository with idempotent writes and rollback on save failure."""

    def __init__(self, store: AttemptStore) -> None:
        self._store = store
        self._lock = asyncio.Lock()
        self._ledger: ExecutionAttemptLedger | None = None

    async def _async_ledger(self) -> ExecutionAttemptLedger:
        if self._ledger is None:
            self._ledger = ExecutionAttemptLedger.from_storage(await self._store.async_load())
        return self._ledger

    async def async_record(self, attempt: ExecutionAttempt) -> AttemptRecordResult:
        async with self._lock:
            current = await self._async_ledger()
            candidate = ExecutionAttemptLedger(current.attempts)
            result = candidate.record(attempt)
            if result.created:
                await self._store.async_save(candidate.as_storage())
                self._ledger = candidate
            return result

    async def async_list(self) -> tuple[ExecutionAttempt, ...]:
        async with self._lock:
            return (await self._async_ledger()).attempts

    async def async_get_by_approval_id(self, approval_id: str) -> ExecutionAttempt | None:
        async with self._lock:
            return (await self._async_ledger()).get_by_approval_id(approval_id)


def home_assistant_attempt_repository(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionAttemptRepository:
    """Create a repository backed by Home Assistant storage for one config entry."""
    store = Store(hass, ATTEMPT_STORAGE_VERSION, attempt_storage_key(entry_id))
    return ExecutionAttemptRepository(store)
