"""Durable pre-lifecycle cancellation tombstones for FRAKON Energy pending runs.

A cancellation tombstone revokes exactly one immutable pending run before a start
lifecycle exists. It never deletes execution audit, creates authority, or calls a
Home Assistant service. The pending scheduler treats a persisted tombstone as a
permanent no-start decision for that exact attempt.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import re
from typing import Any, Protocol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .load_execution_pending_run import ExecutionPendingRun

CANCELLATION_STORAGE_VERSION = 1
CANCELLATION_SCHEMA_VERSION = 1
CANCELLATION_REASON_USER = "user_cancelled_before_lifecycle"
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_RUNTIME_KEY = "load_execution_pending_run_cancellation_repositories_by_entry"


class PendingRunCancellationError(ValueError):
    """Raised when a pending-run cancellation artifact is invalid."""


class PendingRunCancellationConflictError(PendingRunCancellationError):
    """Raised when one attempt is rebound to a different cancellation scope."""


class CancellationStore(Protocol):
    async def async_load(self) -> dict[str, Any] | None: ...
    async def async_save(self, data: dict[str, Any]) -> None: ...


def cancellation_storage_key(entry_id: str) -> str:
    if not entry_id:
        raise PendingRunCancellationError("entry_id is required")
    digest = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:20]
    return f"{DOMAIN}.load_execution_pending_run_cancellations.{digest}"


def _aware(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as err:
        raise PendingRunCancellationError(f"{field} must be ISO-8601") from err
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PendingRunCancellationError(f"{field} must be timezone-aware")
    return parsed


def _cancellation_id(entry_id: str, attempt_id: str, pending_run_id: str) -> str:
    payload = "\0".join((entry_id, attempt_id, pending_run_id, CANCELLATION_REASON_USER))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class PendingRunCancellation:
    cancellation_id: str
    entry_id: str
    attempt_id: str
    pending_run_id: str
    profile_id: str
    entity_id: str
    plan_digest: str
    starts_at: str
    ends_at: str
    pending_created_at: int
    cancelled_at: int
    cancelled_by: str | None
    reason: str = CANCELLATION_REASON_USER
    service_call_performed: bool = False
    execution_performed: bool = False
    executor_available: bool = False

    def validated(self) -> "PendingRunCancellation":
        if not _HEX_32.fullmatch(self.cancellation_id):
            raise PendingRunCancellationError("cancellation_id must be a 32-character hex digest")
        if not self.entry_id or not self.attempt_id or not self.pending_run_id:
            raise PendingRunCancellationError("entry/attempt/pending identity is required")
        if not _HEX_32.fullmatch(self.pending_run_id):
            raise PendingRunCancellationError("pending_run_id must be a 32-character hex digest")
        if not self.profile_id or not self.entity_id:
            raise PendingRunCancellationError("profile_id and entity_id are required")
        if not _HEX_64.fullmatch(self.plan_digest):
            raise PendingRunCancellationError("plan_digest must be SHA-256 hex")
        starts = _aware(self.starts_at, "starts_at")
        ends = _aware(self.ends_at, "ends_at")
        if ends <= starts:
            raise PendingRunCancellationError("ends_at must be after starts_at")
        if self.pending_created_at < 0 or self.cancelled_at < self.pending_created_at:
            raise PendingRunCancellationError("cancellation timestamps are invalid")
        if self.cancelled_by is not None and not self.cancelled_by.strip():
            raise PendingRunCancellationError("cancelled_by cannot be blank")
        if self.reason != CANCELLATION_REASON_USER:
            raise PendingRunCancellationError("unsupported cancellation reason")
        expected = _cancellation_id(self.entry_id, self.attempt_id, self.pending_run_id)
        if self.cancellation_id != expected:
            raise PendingRunCancellationError("cancellation identity does not match immutable scope")
        if self.service_call_performed or self.execution_performed or self.executor_available:
            raise PendingRunCancellationError("cancellation cannot represent execution")
        return self

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_pending_run(
        cls,
        pending: ExecutionPendingRun,
        *,
        cancelled_at: int,
        cancelled_by: str | None,
    ) -> "PendingRunCancellation":
        pending.validated()
        return cls(
            cancellation_id=_cancellation_id(
                pending.entry_id,
                pending.attempt_id,
                pending.pending_run_id,
            ),
            entry_id=pending.entry_id,
            attempt_id=pending.attempt_id,
            pending_run_id=pending.pending_run_id,
            profile_id=pending.profile_id,
            entity_id=pending.entity_id,
            plan_digest=pending.plan_digest,
            starts_at=pending.plan.starts_at,
            ends_at=pending.plan.ends_at,
            pending_created_at=pending.created_at,
            cancelled_at=cancelled_at,
            cancelled_by=cancelled_by,
        ).validated()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PendingRunCancellation":
        try:
            return cls(
                cancellation_id=str(value["cancellation_id"]),
                entry_id=str(value["entry_id"]),
                attempt_id=str(value["attempt_id"]),
                pending_run_id=str(value["pending_run_id"]),
                profile_id=str(value["profile_id"]),
                entity_id=str(value["entity_id"]),
                plan_digest=str(value["plan_digest"]),
                starts_at=str(value["starts_at"]),
                ends_at=str(value["ends_at"]),
                pending_created_at=int(value["pending_created_at"]),
                cancelled_at=int(value["cancelled_at"]),
                cancelled_by=(
                    str(value["cancelled_by"])
                    if value.get("cancelled_by") is not None
                    else None
                ),
                reason=str(value.get("reason", CANCELLATION_REASON_USER)),
                service_call_performed=bool(value.get("service_call_performed", False)),
                execution_performed=bool(value.get("execution_performed", False)),
                executor_available=bool(value.get("executor_available", False)),
            ).validated()
        except (KeyError, TypeError, ValueError) as err:
            if isinstance(err, PendingRunCancellationError):
                raise
            raise PendingRunCancellationError("invalid persisted cancellation") from err


@dataclass(frozen=True, slots=True)
class PendingRunCancellationRecordResult:
    cancellation: PendingRunCancellation
    created: bool
    idempotent_replay: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "cancellation": self.cancellation.as_dict(),
            "created": self.created,
            "idempotent_replay": self.idempotent_replay,
            "service_call_performed": False,
            "execution_performed": False,
            "executor_available": False,
        }


def _same_scope(left: PendingRunCancellation, right: PendingRunCancellation) -> bool:
    return (
        left.cancellation_id == right.cancellation_id
        and left.entry_id == right.entry_id
        and left.attempt_id == right.attempt_id
        and left.pending_run_id == right.pending_run_id
        and left.profile_id == right.profile_id
        and left.entity_id == right.entity_id
        and left.plan_digest == right.plan_digest
        and left.starts_at == right.starts_at
        and left.ends_at == right.ends_at
        and left.pending_created_at == right.pending_created_at
        and left.reason == right.reason
    )


class PendingRunCancellationLedger:
    def __init__(self, records: tuple[PendingRunCancellation, ...] = ()) -> None:
        self._by_attempt: dict[str, PendingRunCancellation] = {}
        for raw in records:
            record = raw.validated()
            if record.attempt_id in self._by_attempt:
                raise PendingRunCancellationError(
                    f"duplicate cancellation for attempt: {record.attempt_id}"
                )
            self._by_attempt[record.attempt_id] = record

    @property
    def records(self) -> tuple[PendingRunCancellation, ...]:
        return tuple(
            sorted(
                self._by_attempt.values(),
                key=lambda item: (item.cancelled_at, item.cancellation_id),
            )
        )

    def get_by_attempt_id(self, attempt_id: str) -> PendingRunCancellation | None:
        return self._by_attempt.get(attempt_id)

    def record(
        self,
        cancellation: PendingRunCancellation,
    ) -> PendingRunCancellationRecordResult:
        candidate = cancellation.validated()
        existing = self._by_attempt.get(candidate.attempt_id)
        if existing is not None:
            if _same_scope(existing, candidate):
                return PendingRunCancellationRecordResult(
                    existing,
                    created=False,
                    idempotent_replay=True,
                )
            raise PendingRunCancellationConflictError(
                "attempt already has a cancellation for different immutable scope"
            )
        self._by_attempt[candidate.attempt_id] = candidate
        return PendingRunCancellationRecordResult(
            candidate,
            created=True,
            idempotent_replay=False,
        )

    def as_storage(self) -> dict[str, Any]:
        return {
            "schema_version": CANCELLATION_SCHEMA_VERSION,
            "cancellations": [record.as_dict() for record in self.records],
        }

    @classmethod
    def from_storage(
        cls,
        value: dict[str, Any] | None,
    ) -> "PendingRunCancellationLedger":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise PendingRunCancellationError("cancellation storage must be an object")
        if value.get("schema_version") != CANCELLATION_SCHEMA_VERSION:
            raise PendingRunCancellationError("unsupported cancellation storage schema")
        raw = value.get("cancellations", [])
        if not isinstance(raw, list):
            raise PendingRunCancellationError("cancellations must be a list")
        return cls(
            tuple(
                PendingRunCancellation.from_dict(item)
                for item in raw
                if isinstance(item, dict)
            )
        )


class PendingRunCancellationRepository:
    def __init__(self, store: CancellationStore) -> None:
        self._store = store
        self._lock = asyncio.Lock()
        self._ledger: PendingRunCancellationLedger | None = None

    async def _async_ledger(self) -> PendingRunCancellationLedger:
        if self._ledger is None:
            self._ledger = PendingRunCancellationLedger.from_storage(
                await self._store.async_load()
            )
        return self._ledger

    async def async_record(
        self,
        cancellation: PendingRunCancellation,
    ) -> PendingRunCancellationRecordResult:
        async with self._lock:
            current = await self._async_ledger()
            candidate = PendingRunCancellationLedger(current.records)
            result = candidate.record(cancellation)
            if result.created:
                await self._store.async_save(candidate.as_storage())
                self._ledger = candidate
            return result

    async def async_get_by_attempt_id(
        self,
        attempt_id: str,
    ) -> PendingRunCancellation | None:
        async with self._lock:
            return (await self._async_ledger()).get_by_attempt_id(attempt_id)

    async def async_list(self) -> tuple[PendingRunCancellation, ...]:
        async with self._lock:
            return (await self._async_ledger()).records


def cancellation_repository(
    hass: HomeAssistant,
    entry_id: str,
) -> PendingRunCancellationRepository:
    if not entry_id:
        raise PendingRunCancellationError("entry_id is required")
    domain_data = hass.data.setdefault(DOMAIN, {})
    repositories = domain_data.get(_RUNTIME_KEY)
    if not isinstance(repositories, dict):
        repositories = {}
        domain_data[_RUNTIME_KEY] = repositories
    repository = repositories.get(entry_id)
    if isinstance(repository, PendingRunCancellationRepository):
        return repository
    repository = PendingRunCancellationRepository(
        Store(hass, CANCELLATION_STORAGE_VERSION, cancellation_storage_key(entry_id))
    )
    repositories[entry_id] = repository
    return repository
