"""Durable inert pending runs for restart-safe FRAKON Energy execution scheduling.

A pending run stores the exact already-consumed attempt, immutable action binding
and approved plan before the short execution start window opens. It is not an
approval, lifecycle, stop lease or executor and it can never call a Home
Assistant service by itself.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import hashlib
import re
from typing import Any, Protocol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .load_execution_action_snapshot import ExecutionActionSnapshot
from .load_execution_attempt import ExecutionAttempt
from .load_execution_lifecycle import ExecutionPlanSnapshot

PENDING_RUN_STORAGE_VERSION = 1
PENDING_RUN_SCHEMA_VERSION = 1
PENDING_RUN_SCHEDULED = "scheduled"

_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_STARTS = {
    ("switch", "turn_on"),
    ("input_boolean", "turn_on"),
}


class PendingRunError(ValueError):
    """Raised when a durable pending run is invalid."""


class PendingRunConflictError(PendingRunError):
    """Raised when one attempt is rebound to a different immutable run."""


class PendingRunStore(Protocol):
    async def async_load(self) -> dict[str, Any] | None: ...
    async def async_save(self, data: dict[str, Any]) -> None: ...


def pending_run_storage_key(entry_id: str) -> str:
    if not entry_id:
        raise PendingRunError("entry_id is required")
    digest = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:20]
    return f"{DOMAIN}.load_execution_pending_runs.{digest}"


def _pending_run_id(
    *,
    entry_id: str,
    attempt_id: str,
    action_snapshot_id: str,
    approval_snapshot_digest: str,
    plan_digest: str,
) -> str:
    payload = "\0".join(
        (
            entry_id,
            attempt_id,
            action_snapshot_id,
            approval_snapshot_digest,
            plan_digest,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class ExecutionPendingRun:
    """Immutable exact approved plan waiting for its server start window."""

    pending_run_id: str
    entry_id: str
    attempt_id: str
    action_snapshot_id: str
    profile_id: str
    entity_id: str
    approval_snapshot_digest: str
    plan_digest: str
    plan: ExecutionPlanSnapshot
    service_domain: str
    service_name: str
    desired_state: str
    status: str
    created_at: int
    service_call_performed: bool = False
    executor_available: bool = False

    def validated(self) -> "ExecutionPendingRun":
        if not _HEX_32.fullmatch(self.pending_run_id):
            raise PendingRunError("pending_run_id must be a 32-character hex digest")
        if not self.entry_id or not self.attempt_id or not self.action_snapshot_id:
            raise PendingRunError("entry/attempt/action snapshot identity is required")
        if not self.profile_id or not self.entity_id:
            raise PendingRunError("profile_id and entity_id are required")
        if not _HEX_64.fullmatch(self.approval_snapshot_digest):
            raise PendingRunError("approval_snapshot_digest must be SHA-256 hex")
        if not _HEX_64.fullmatch(self.plan_digest):
            raise PendingRunError("plan_digest must be SHA-256 hex")
        plan = self.plan.validated()
        if plan.digest() != self.plan_digest:
            raise PendingRunError("pending run plan digest does not match plan payload")
        if plan.load_id != self.profile_id:
            raise PendingRunError("pending run plan does not match profile")
        if self.status != PENDING_RUN_SCHEDULED:
            raise PendingRunError("pending run status must remain scheduled/inert")
        if (self.service_domain, self.service_name) not in _ALLOWED_STARTS:
            raise PendingRunError("pending run start action is not allowlisted")
        if not self.entity_id.startswith(f"{self.service_domain}."):
            raise PendingRunError("pending run entity domain does not match start service")
        if self.desired_state != "on":
            raise PendingRunError("pending run desired state must be on")
        if self.created_at < 0:
            raise PendingRunError("created_at must be non-negative")
        if self.service_call_performed or self.executor_available:
            raise PendingRunError("pending run cannot represent execution")
        expected_id = _pending_run_id(
            entry_id=self.entry_id,
            attempt_id=self.attempt_id,
            action_snapshot_id=self.action_snapshot_id,
            approval_snapshot_digest=self.approval_snapshot_digest,
            plan_digest=self.plan_digest,
        )
        if self.pending_run_id != expected_id:
            raise PendingRunError("pending run identity does not match immutable binding")
        return self

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["plan"] = self.plan.as_dict()
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExecutionPendingRun":
        try:
            plan_value = value["plan"]
            if not isinstance(plan_value, dict):
                raise PendingRunError("pending run plan must be an object")
            return cls(
                pending_run_id=str(value["pending_run_id"]),
                entry_id=str(value["entry_id"]),
                attempt_id=str(value["attempt_id"]),
                action_snapshot_id=str(value["action_snapshot_id"]),
                profile_id=str(value["profile_id"]),
                entity_id=str(value["entity_id"]),
                approval_snapshot_digest=str(value["approval_snapshot_digest"]),
                plan_digest=str(value["plan_digest"]),
                plan=ExecutionPlanSnapshot.from_dict(plan_value),
                service_domain=str(value["service_domain"]),
                service_name=str(value["service_name"]),
                desired_state=str(value["desired_state"]),
                status=str(value["status"]),
                created_at=int(value["created_at"]),
                service_call_performed=bool(value.get("service_call_performed", False)),
                executor_available=bool(value.get("executor_available", False)),
            ).validated()
        except (KeyError, TypeError, ValueError) as err:
            if isinstance(err, PendingRunError):
                raise
            raise PendingRunError("invalid persisted pending run") from err

    @classmethod
    def from_records(
        cls,
        *,
        attempt: ExecutionAttempt,
        action_snapshot: ExecutionActionSnapshot,
        plan: ExecutionPlanSnapshot,
        created_at: int,
    ) -> "ExecutionPendingRun":
        attempt.validated()
        action_snapshot.validated()
        plan.validated()
        if action_snapshot.attempt_id != attempt.attempt_id:
            raise PendingRunError("action snapshot attempt does not match pending run attempt")
        if action_snapshot.entry_id != attempt.entry_id:
            raise PendingRunError("action snapshot entry does not match pending run attempt")
        if action_snapshot.profile_id != attempt.profile_id:
            raise PendingRunError("action snapshot profile does not match pending run attempt")
        if action_snapshot.entity_id != attempt.entity_id:
            raise PendingRunError("action snapshot entity does not match pending run attempt")
        if action_snapshot.approval_snapshot_digest != attempt.snapshot_digest:
            raise PendingRunError("action snapshot approval scope does not match attempt")
        if plan.load_id != attempt.profile_id:
            raise PendingRunError("plan does not match pending run profile")
        plan_digest = plan.digest()
        pending_id = _pending_run_id(
            entry_id=attempt.entry_id,
            attempt_id=attempt.attempt_id,
            action_snapshot_id=action_snapshot.snapshot_id,
            approval_snapshot_digest=attempt.snapshot_digest,
            plan_digest=plan_digest,
        )
        return cls(
            pending_run_id=pending_id,
            entry_id=attempt.entry_id,
            attempt_id=attempt.attempt_id,
            action_snapshot_id=action_snapshot.snapshot_id,
            profile_id=attempt.profile_id,
            entity_id=attempt.entity_id,
            approval_snapshot_digest=attempt.snapshot_digest,
            plan_digest=plan_digest,
            plan=plan,
            service_domain=action_snapshot.service_domain,
            service_name=action_snapshot.service_name,
            desired_state=action_snapshot.desired_state,
            status=PENDING_RUN_SCHEDULED,
            created_at=created_at,
        ).validated()


@dataclass(frozen=True, slots=True)
class PendingRunRecordResult:
    pending_run: ExecutionPendingRun
    created: bool
    idempotent_replay: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "pending_run": self.pending_run.as_dict(),
            "created": self.created,
            "idempotent_replay": self.idempotent_replay,
            "service_call_performed": False,
            "execution_performed": False,
            "executor_available": False,
        }


class ExecutionPendingRunLedger:
    def __init__(self, records: tuple[ExecutionPendingRun, ...] = ()) -> None:
        self._by_attempt: dict[str, ExecutionPendingRun] = {}
        self._by_id: dict[str, ExecutionPendingRun] = {}
        for record in records:
            item = record.validated()
            if item.attempt_id in self._by_attempt:
                raise PendingRunError(
                    f"duplicate pending run for attempt: {item.attempt_id}"
                )
            if item.pending_run_id in self._by_id:
                raise PendingRunError(f"duplicate pending run id: {item.pending_run_id}")
            self._by_attempt[item.attempt_id] = item
            self._by_id[item.pending_run_id] = item

    @property
    def records(self) -> tuple[ExecutionPendingRun, ...]:
        return tuple(
            sorted(
                self._by_id.values(),
                key=lambda item: (item.created_at, item.pending_run_id),
            )
        )

    def get_by_attempt_id(self, attempt_id: str) -> ExecutionPendingRun | None:
        return self._by_attempt.get(attempt_id)

    def record(self, pending_run: ExecutionPendingRun) -> PendingRunRecordResult:
        candidate = pending_run.validated()
        existing = self._by_attempt.get(candidate.attempt_id)
        if existing is not None:
            if existing == candidate or existing.pending_run_id == candidate.pending_run_id:
                return PendingRunRecordResult(existing, created=False, idempotent_replay=True)
            raise PendingRunConflictError(
                "execution attempt already has a different immutable pending run"
            )
        if candidate.pending_run_id in self._by_id:
            raise PendingRunConflictError("pending run ID belongs to another execution attempt")
        self._by_attempt[candidate.attempt_id] = candidate
        self._by_id[candidate.pending_run_id] = candidate
        return PendingRunRecordResult(candidate, created=True, idempotent_replay=False)

    def as_storage(self) -> dict[str, Any]:
        return {
            "schema_version": PENDING_RUN_SCHEMA_VERSION,
            "pending_runs": [record.as_dict() for record in self.records],
        }

    @classmethod
    def from_storage(cls, value: dict[str, Any] | None) -> "ExecutionPendingRunLedger":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise PendingRunError("pending run storage must be an object")
        if value.get("schema_version") != PENDING_RUN_SCHEMA_VERSION:
            raise PendingRunError("unsupported pending run storage schema")
        raw = value.get("pending_runs", [])
        if not isinstance(raw, list):
            raise PendingRunError("pending_runs must be a list")
        records: list[ExecutionPendingRun] = []
        for item in raw:
            if not isinstance(item, dict):
                raise PendingRunError("pending run record must be an object")
            records.append(ExecutionPendingRun.from_dict(item))
        return cls(tuple(records))


class ExecutionPendingRunRepository:
    def __init__(self, store: PendingRunStore) -> None:
        self._store = store
        self._lock = asyncio.Lock()
        self._ledger: ExecutionPendingRunLedger | None = None

    async def _async_ledger(self) -> ExecutionPendingRunLedger:
        if self._ledger is None:
            self._ledger = ExecutionPendingRunLedger.from_storage(
                await self._store.async_load()
            )
        return self._ledger

    async def async_record(
        self,
        pending_run: ExecutionPendingRun,
    ) -> PendingRunRecordResult:
        async with self._lock:
            current = await self._async_ledger()
            candidate = ExecutionPendingRunLedger(current.records)
            result = candidate.record(pending_run)
            if result.created:
                await self._store.async_save(candidate.as_storage())
                self._ledger = candidate
            return result

    async def async_get_by_attempt_id(
        self,
        attempt_id: str,
    ) -> ExecutionPendingRun | None:
        async with self._lock:
            return (await self._async_ledger()).get_by_attempt_id(attempt_id)

    async def async_list(self) -> tuple[ExecutionPendingRun, ...]:
        async with self._lock:
            return (await self._async_ledger()).records


def home_assistant_pending_run_repository(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionPendingRunRepository:
    return ExecutionPendingRunRepository(
        Store(
            hass,
            PENDING_RUN_STORAGE_VERSION,
            pending_run_storage_key(entry_id),
        )
    )
