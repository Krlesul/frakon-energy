"""Durable stop-execution lifecycle for bounded FRAKON Energy runs.

The stop lifecycle is created before any future start service call, after the
start lifecycle has durably entered ``dispatching`` and an exact armed stop
lease already exists. This module contains no Home Assistant service call.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, replace
from datetime import datetime
import hashlib
import re
from typing import Any, Protocol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .load_execution_lifecycle import (
    CALL_CONFIRMED,
    CALL_NOT_STARTED,
    CALL_UNKNOWN,
    STATE_DISPATCHING as START_STATE_DISPATCHING,
    ExecutionLifecycleRecord,
)
from .load_execution_stop_lease import (
    STOP_LEASE_ARMED,
    ExecutionStopLease,
)

STOP_LIFECYCLE_STORAGE_VERSION = 1
STOP_LIFECYCLE_SCHEMA_VERSION = 1

STOP_STATE_OWNED = "owned"
STOP_STATE_DISPATCHING = "dispatching"
STOP_STATE_DISPATCHED = "dispatched"
STOP_STATE_RECOVERY_REQUIRED = "recovery_required"
STOP_STATE_VERIFIED = "verified"
STOP_STATE_SATISFIED = "satisfied"
STOP_STATE_FAILED = "failed"
STOP_STATES = (
    STOP_STATE_OWNED,
    STOP_STATE_DISPATCHING,
    STOP_STATE_DISPATCHED,
    STOP_STATE_RECOVERY_REQUIRED,
    STOP_STATE_VERIFIED,
    STOP_STATE_SATISFIED,
    STOP_STATE_FAILED,
)

STOP_CALL_NOT_STARTED = "not_started"
STOP_CALL_UNKNOWN = "unknown"
STOP_CALL_CONFIRMED = "confirmed"
STOP_CALL_FAILED = "failed"
STOP_CALL_STATUSES = (
    STOP_CALL_NOT_STARTED,
    STOP_CALL_UNKNOWN,
    STOP_CALL_CONFIRMED,
    STOP_CALL_FAILED,
)

STOP_VERIFY_PENDING = "pending"
STOP_VERIFY_CONFIRMED = "confirmed"
STOP_VERIFY_FAILED = "failed"
STOP_VERIFY_STATUSES = (
    STOP_VERIFY_PENDING,
    STOP_VERIFY_CONFIRMED,
    STOP_VERIFY_FAILED,
)

_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class StopLifecycleError(ValueError):
    """Raised for invalid stop lifecycle records or transitions."""


class StopLifecycleConflictError(StopLifecycleError):
    """Raised when a stop obligation is rebound to different immutable scope."""


class StopLifecycleStore(Protocol):
    async def async_load(self) -> dict[str, Any] | None: ...
    async def async_save(self, data: dict[str, Any]) -> None: ...


def _stop_lifecycle_id(*, lease_id: str, start_lifecycle_id: str) -> str:
    payload = f"{lease_id}\0{start_lifecycle_id}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def stop_lifecycle_storage_key(entry_id: str) -> str:
    if not entry_id:
        raise StopLifecycleError("entry_id is required")
    digest = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:20]
    return f"{DOMAIN}.load_execution_stop_lifecycle.{digest}"


def _aware_timestamp(value: str, field: str) -> int:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as err:
        raise StopLifecycleError(f"{field} must be ISO-8601 datetime") from err
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StopLifecycleError(f"{field} must include timezone offset")
    return int(parsed.timestamp())


@dataclass(frozen=True, slots=True)
class ExecutionStopLifecycleRecord:
    """Crash-safe mutable lifecycle for one immutable stop lease."""

    stop_lifecycle_id: str
    lease_id: str
    entry_id: str
    start_lifecycle_id: str
    attempt_id: str
    action_snapshot_id: str
    profile_id: str
    entity_id: str
    approval_snapshot_digest: str
    plan_digest: str
    starts_at: str
    ends_at: str
    service_domain: str
    service_name: str
    desired_state: str
    state: str
    service_call_status: str
    verification_status: str
    created_at: int
    updated_at: int
    dispatch_attempts: int = 0
    dispatch_started_at: int | None = None
    dispatch_confirmed_at: int | None = None
    verified_at: int | None = None
    satisfied_at: int | None = None
    failed_at: int | None = None
    failure_reason: str | None = None
    executor_available: bool = False

    def validated(self) -> "ExecutionStopLifecycleRecord":
        if not _HEX_32.fullmatch(self.stop_lifecycle_id):
            raise StopLifecycleError("stop_lifecycle_id must be a 32-character hex digest")
        if not _HEX_32.fullmatch(self.lease_id):
            raise StopLifecycleError("lease_id must be a 32-character hex digest")
        if not self.entry_id or not self.start_lifecycle_id or not self.attempt_id:
            raise StopLifecycleError("entry/start lifecycle/attempt identity is required")
        if not self.action_snapshot_id or not self.profile_id or not self.entity_id:
            raise StopLifecycleError("action/profile/entity identity is required")
        if not _HEX_64.fullmatch(self.approval_snapshot_digest):
            raise StopLifecycleError("approval_snapshot_digest must be SHA-256 hex")
        if not _HEX_64.fullmatch(self.plan_digest):
            raise StopLifecycleError("plan_digest must be SHA-256 hex")
        starts = _aware_timestamp(self.starts_at, "starts_at")
        ends = _aware_timestamp(self.ends_at, "ends_at")
        if ends <= starts:
            raise StopLifecycleError("ends_at must be after starts_at")
        if (self.service_domain, self.service_name) not in {
            ("switch", "turn_off"),
            ("input_boolean", "turn_off"),
        }:
            raise StopLifecycleError("stop service mapping is not allowlisted")
        if self.desired_state != "off":
            raise StopLifecycleError("stop desired state must be off")
        if self.state not in STOP_STATES:
            raise StopLifecycleError(f"unsupported stop lifecycle state: {self.state}")
        if self.service_call_status not in STOP_CALL_STATUSES:
            raise StopLifecycleError("unsupported stop service_call_status")
        if self.verification_status not in STOP_VERIFY_STATUSES:
            raise StopLifecycleError("unsupported stop verification_status")
        if self.created_at < 0 or self.updated_at < self.created_at:
            raise StopLifecycleError("stop lifecycle timestamps are invalid")
        if self.dispatch_attempts < 0:
            raise StopLifecycleError("dispatch_attempts cannot be negative")
        if self.executor_available:
            raise StopLifecycleError("stop lifecycle cannot advertise an executor")
        expected_id = _stop_lifecycle_id(
            lease_id=self.lease_id,
            start_lifecycle_id=self.start_lifecycle_id,
        )
        if self.stop_lifecycle_id != expected_id:
            raise StopLifecycleError("stop lifecycle identity does not match immutable binding")

        if self.state == STOP_STATE_OWNED:
            if self.service_call_status != STOP_CALL_NOT_STARTED:
                raise StopLifecycleError("owned stop lifecycle cannot contain call evidence")
            if self.verification_status != STOP_VERIFY_PENDING:
                raise StopLifecycleError("owned stop lifecycle must be pending verification")
            if self.dispatch_attempts != 0 or self.dispatch_started_at is not None:
                raise StopLifecycleError("owned stop lifecycle cannot contain dispatch attempts")
        elif self.state == STOP_STATE_DISPATCHING:
            if self.service_call_status != STOP_CALL_UNKNOWN:
                raise StopLifecycleError("dispatching stop lifecycle must preserve unknown call status")
            if self.verification_status != STOP_VERIFY_PENDING:
                raise StopLifecycleError("dispatching stop lifecycle must be pending verification")
            if self.dispatch_attempts < 1 or self.dispatch_started_at is None:
                raise StopLifecycleError("dispatching stop lifecycle requires dispatch evidence")
        elif self.state == STOP_STATE_DISPATCHED:
            if self.service_call_status != STOP_CALL_CONFIRMED:
                raise StopLifecycleError("dispatched stop lifecycle requires confirmed call evidence")
            if self.verification_status != STOP_VERIFY_PENDING:
                raise StopLifecycleError("dispatched stop lifecycle must await verification")
            if self.dispatch_started_at is None or self.dispatch_confirmed_at is None:
                raise StopLifecycleError("dispatched stop lifecycle requires timestamps")
        elif self.state == STOP_STATE_RECOVERY_REQUIRED:
            if self.service_call_status != STOP_CALL_UNKNOWN:
                raise StopLifecycleError("recovery stop lifecycle must preserve unknown call evidence")
            if self.verification_status != STOP_VERIFY_PENDING or self.dispatch_started_at is None:
                raise StopLifecycleError("recovery stop lifecycle requires pending dispatch evidence")
        elif self.state == STOP_STATE_VERIFIED:
            if self.verification_status != STOP_VERIFY_CONFIRMED or self.verified_at is None:
                raise StopLifecycleError("verified stop lifecycle requires verification evidence")
            if self.service_call_status not in (STOP_CALL_CONFIRMED, STOP_CALL_UNKNOWN):
                raise StopLifecycleError("verified stop lifecycle has impossible call evidence")
        elif self.state == STOP_STATE_SATISFIED:
            if self.service_call_status != STOP_CALL_NOT_STARTED:
                raise StopLifecycleError("satisfied no-op stop lifecycle must not contain a call")
            if self.verification_status != STOP_VERIFY_CONFIRMED or self.satisfied_at is None:
                raise StopLifecycleError("satisfied stop lifecycle requires observed off state")
            if self.dispatch_attempts != 0:
                raise StopLifecycleError("satisfied no-op stop lifecycle cannot contain dispatch attempts")
        elif self.state == STOP_STATE_FAILED:
            if self.verification_status != STOP_VERIFY_FAILED:
                raise StopLifecycleError("failed stop lifecycle requires failed verification")
            if self.failed_at is None or not self.failure_reason:
                raise StopLifecycleError("failed stop lifecycle requires failure evidence")
        return self

    @classmethod
    def owned(
        cls,
        *,
        lease: ExecutionStopLease,
        start_lifecycle: ExecutionLifecycleRecord,
        created_at: int,
    ) -> "ExecutionStopLifecycleRecord":
        lease.validated()
        start_lifecycle.validated()
        if lease.status != STOP_LEASE_ARMED:
            raise StopLifecycleError("stop lease must be armed")
        if start_lifecycle.state != START_STATE_DISPATCHING:
            raise StopLifecycleError("start lifecycle must be dispatching before stop ownership")
        if start_lifecycle.service_call_status != CALL_UNKNOWN:
            raise StopLifecycleError("start lifecycle dispatch evidence must be unknown before call")
        if (
            lease.entry_id != start_lifecycle.entry_id
            or lease.lifecycle_id != start_lifecycle.lifecycle_id
            or lease.attempt_id != start_lifecycle.attempt_id
            or lease.action_snapshot_id != start_lifecycle.action_snapshot_id
            or lease.profile_id != start_lifecycle.profile_id
            or lease.entity_id != start_lifecycle.entity_id
            or lease.approval_snapshot_digest != start_lifecycle.approval_snapshot_digest
            or lease.plan_digest != start_lifecycle.plan_digest
            or lease.starts_at != start_lifecycle.plan.starts_at
            or lease.ends_at != start_lifecycle.plan.ends_at
        ):
            raise StopLifecycleError("stop lease and start lifecycle immutable scope do not match")
        if created_at < start_lifecycle.updated_at:
            raise StopLifecycleError("stop ownership cannot predate start dispatch persistence")
        return cls(
            stop_lifecycle_id=_stop_lifecycle_id(
                lease_id=lease.lease_id,
                start_lifecycle_id=start_lifecycle.lifecycle_id,
            ),
            lease_id=lease.lease_id,
            entry_id=lease.entry_id,
            start_lifecycle_id=start_lifecycle.lifecycle_id,
            attempt_id=lease.attempt_id,
            action_snapshot_id=lease.action_snapshot_id,
            profile_id=lease.profile_id,
            entity_id=lease.entity_id,
            approval_snapshot_digest=lease.approval_snapshot_digest,
            plan_digest=lease.plan_digest,
            starts_at=lease.starts_at,
            ends_at=lease.ends_at,
            service_domain=lease.service_domain,
            service_name=lease.service_name,
            desired_state=lease.desired_state,
            state=STOP_STATE_OWNED,
            service_call_status=STOP_CALL_NOT_STARTED,
            verification_status=STOP_VERIFY_PENDING,
            created_at=created_at,
            updated_at=created_at,
        ).validated()

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["service_call_performed"] = (
            True if self.service_call_status == STOP_CALL_CONFIRMED
            else None if self.service_call_status == STOP_CALL_UNKNOWN
            else False
        )
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExecutionStopLifecycleRecord":
        try:
            return cls(
                stop_lifecycle_id=str(value["stop_lifecycle_id"]),
                lease_id=str(value["lease_id"]),
                entry_id=str(value["entry_id"]),
                start_lifecycle_id=str(value["start_lifecycle_id"]),
                attempt_id=str(value["attempt_id"]),
                action_snapshot_id=str(value["action_snapshot_id"]),
                profile_id=str(value["profile_id"]),
                entity_id=str(value["entity_id"]),
                approval_snapshot_digest=str(value["approval_snapshot_digest"]),
                plan_digest=str(value["plan_digest"]),
                starts_at=str(value["starts_at"]),
                ends_at=str(value["ends_at"]),
                service_domain=str(value["service_domain"]),
                service_name=str(value["service_name"]),
                desired_state=str(value["desired_state"]),
                state=str(value["state"]),
                service_call_status=str(value["service_call_status"]),
                verification_status=str(value["verification_status"]),
                created_at=int(value["created_at"]),
                updated_at=int(value["updated_at"]),
                dispatch_attempts=int(value.get("dispatch_attempts", 0)),
                dispatch_started_at=int(value["dispatch_started_at"]) if value.get("dispatch_started_at") is not None else None,
                dispatch_confirmed_at=int(value["dispatch_confirmed_at"]) if value.get("dispatch_confirmed_at") is not None else None,
                verified_at=int(value["verified_at"]) if value.get("verified_at") is not None else None,
                satisfied_at=int(value["satisfied_at"]) if value.get("satisfied_at") is not None else None,
                failed_at=int(value["failed_at"]) if value.get("failed_at") is not None else None,
                failure_reason=str(value["failure_reason"]) if value.get("failure_reason") is not None else None,
                executor_available=bool(value.get("executor_available", False)),
            ).validated()
        except (KeyError, TypeError, ValueError) as err:
            if isinstance(err, StopLifecycleError):
                raise
            raise StopLifecycleError("invalid persisted stop lifecycle record") from err


def _transition_time(record: ExecutionStopLifecycleRecord, now: int) -> int:
    if now < record.updated_at:
        raise StopLifecycleError("stop lifecycle transition timestamp cannot move backwards")
    return now


def begin_stop_dispatch(
    record: ExecutionStopLifecycleRecord,
    *,
    now: int,
) -> ExecutionStopLifecycleRecord:
    record.validated()
    if record.state not in (STOP_STATE_OWNED, STOP_STATE_RECOVERY_REQUIRED):
        raise StopLifecycleError(f"cannot begin stop dispatch from state {record.state}")
    timestamp = _transition_time(record, now)
    return replace(
        record,
        state=STOP_STATE_DISPATCHING,
        service_call_status=STOP_CALL_UNKNOWN,
        verification_status=STOP_VERIFY_PENDING,
        updated_at=timestamp,
        dispatch_attempts=record.dispatch_attempts + 1,
        dispatch_started_at=timestamp,
        dispatch_confirmed_at=None,
        failure_reason=None,
    ).validated()


def confirm_stop_dispatch(
    record: ExecutionStopLifecycleRecord,
    *,
    now: int,
) -> ExecutionStopLifecycleRecord:
    record.validated()
    if record.state != STOP_STATE_DISPATCHING:
        raise StopLifecycleError(f"cannot confirm stop dispatch from state {record.state}")
    timestamp = _transition_time(record, now)
    return replace(
        record,
        state=STOP_STATE_DISPATCHED,
        service_call_status=STOP_CALL_CONFIRMED,
        updated_at=timestamp,
        dispatch_confirmed_at=timestamp,
    ).validated()


def require_stop_recovery_after_restart(
    record: ExecutionStopLifecycleRecord,
    *,
    now: int,
) -> ExecutionStopLifecycleRecord:
    record.validated()
    if record.state != STOP_STATE_DISPATCHING:
        return record
    timestamp = _transition_time(record, now)
    return replace(
        record,
        state=STOP_STATE_RECOVERY_REQUIRED,
        service_call_status=STOP_CALL_UNKNOWN,
        updated_at=timestamp,
    ).validated()


def verify_stop_state(
    record: ExecutionStopLifecycleRecord,
    *,
    current_state: str | None,
    now: int,
) -> ExecutionStopLifecycleRecord:
    record.validated()
    if record.state not in (STOP_STATE_DISPATCHED, STOP_STATE_RECOVERY_REQUIRED):
        raise StopLifecycleError(f"cannot verify stop from state {record.state}")
    normalized = current_state.strip().lower() if isinstance(current_state, str) else None
    if normalized != record.desired_state:
        raise StopLifecycleError("current entity state does not match stop desired state")
    timestamp = _transition_time(record, now)
    return replace(
        record,
        state=STOP_STATE_VERIFIED,
        verification_status=STOP_VERIFY_CONFIRMED,
        updated_at=timestamp,
        verified_at=timestamp,
    ).validated()


def satisfy_stop_without_dispatch(
    record: ExecutionStopLifecycleRecord,
    *,
    current_state: str | None,
    now: int,
) -> ExecutionStopLifecycleRecord:
    record.validated()
    if record.state != STOP_STATE_OWNED:
        raise StopLifecycleError(f"cannot satisfy stop without dispatch from state {record.state}")
    normalized = current_state.strip().lower() if isinstance(current_state, str) else None
    if normalized != record.desired_state:
        raise StopLifecycleError("entity is not already in stop desired state")
    timestamp = _transition_time(record, now)
    return replace(
        record,
        state=STOP_STATE_SATISFIED,
        verification_status=STOP_VERIFY_CONFIRMED,
        updated_at=timestamp,
        satisfied_at=timestamp,
    ).validated()


def fail_stop_lifecycle(
    record: ExecutionStopLifecycleRecord,
    *,
    reason: str,
    now: int,
    service_call_status: str | None = None,
) -> ExecutionStopLifecycleRecord:
    record.validated()
    if record.state in (STOP_STATE_VERIFIED, STOP_STATE_SATISFIED, STOP_STATE_FAILED):
        raise StopLifecycleError(f"cannot fail terminal stop state {record.state}")
    if not reason.strip():
        raise StopLifecycleError("stop failure reason is required")
    timestamp = _transition_time(record, now)
    call_status = service_call_status or record.service_call_status
    if call_status not in STOP_CALL_STATUSES:
        raise StopLifecycleError("invalid stop service call status for failure")
    return replace(
        record,
        state=STOP_STATE_FAILED,
        service_call_status=call_status,
        verification_status=STOP_VERIFY_FAILED,
        updated_at=timestamp,
        failed_at=timestamp,
        failure_reason=reason.strip(),
    ).validated()


_ALLOWED_TRANSITIONS = {
    STOP_STATE_OWNED: {STOP_STATE_DISPATCHING, STOP_STATE_SATISFIED, STOP_STATE_FAILED},
    STOP_STATE_DISPATCHING: {STOP_STATE_DISPATCHED, STOP_STATE_RECOVERY_REQUIRED, STOP_STATE_FAILED},
    STOP_STATE_DISPATCHED: {STOP_STATE_VERIFIED, STOP_STATE_FAILED},
    STOP_STATE_RECOVERY_REQUIRED: {STOP_STATE_DISPATCHING, STOP_STATE_VERIFIED, STOP_STATE_FAILED},
    STOP_STATE_VERIFIED: set(),
    STOP_STATE_SATISFIED: set(),
    STOP_STATE_FAILED: set(),
}


def validate_stop_lifecycle_transition(
    previous: ExecutionStopLifecycleRecord,
    updated: ExecutionStopLifecycleRecord,
) -> None:
    previous.validated()
    updated.validated()
    immutable_fields = (
        "stop_lifecycle_id",
        "lease_id",
        "entry_id",
        "start_lifecycle_id",
        "attempt_id",
        "action_snapshot_id",
        "profile_id",
        "entity_id",
        "approval_snapshot_digest",
        "plan_digest",
        "starts_at",
        "ends_at",
        "service_domain",
        "service_name",
        "desired_state",
        "created_at",
    )
    if any(getattr(previous, field) != getattr(updated, field) for field in immutable_fields):
        raise StopLifecycleConflictError("stop lifecycle immutable binding changed")
    if updated.state == previous.state:
        if updated != previous:
            raise StopLifecycleError("same-state stop lifecycle update is not allowed")
        return
    if updated.state not in _ALLOWED_TRANSITIONS[previous.state]:
        raise StopLifecycleError(
            f"invalid stop lifecycle transition: {previous.state} -> {updated.state}"
        )


@dataclass(frozen=True, slots=True)
class StopLifecycleRecordResult:
    record: ExecutionStopLifecycleRecord
    created: bool
    idempotent_replay: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "stop_lifecycle": self.record.as_dict(),
            "created": self.created,
            "idempotent_replay": self.idempotent_replay,
            "executor_available": False,
        }


class ExecutionStopLifecycleLedger:
    def __init__(self, records: tuple[ExecutionStopLifecycleRecord, ...] = ()) -> None:
        self._by_start_lifecycle: dict[str, ExecutionStopLifecycleRecord] = {}
        self._by_id: dict[str, ExecutionStopLifecycleRecord] = {}
        for record in records:
            item = record.validated()
            if item.start_lifecycle_id in self._by_start_lifecycle:
                raise StopLifecycleError(
                    f"duplicate stop lifecycle for start lifecycle: {item.start_lifecycle_id}"
                )
            if item.stop_lifecycle_id in self._by_id:
                raise StopLifecycleError(
                    f"duplicate stop lifecycle id: {item.stop_lifecycle_id}"
                )
            self._by_start_lifecycle[item.start_lifecycle_id] = item
            self._by_id[item.stop_lifecycle_id] = item

    @property
    def records(self) -> tuple[ExecutionStopLifecycleRecord, ...]:
        return tuple(
            sorted(self._by_id.values(), key=lambda item: (item.created_at, item.stop_lifecycle_id))
        )

    def get_by_start_lifecycle_id(
        self,
        start_lifecycle_id: str,
    ) -> ExecutionStopLifecycleRecord | None:
        return self._by_start_lifecycle.get(start_lifecycle_id)

    def create_owned(
        self,
        record: ExecutionStopLifecycleRecord,
    ) -> StopLifecycleRecordResult:
        candidate = record.validated()
        existing = self._by_start_lifecycle.get(candidate.start_lifecycle_id)
        if existing is not None:
            if existing.stop_lifecycle_id == candidate.stop_lifecycle_id:
                return StopLifecycleRecordResult(existing, created=False, idempotent_replay=True)
            raise StopLifecycleConflictError(
                "start lifecycle already has a different stop lifecycle"
            )
        if candidate.state != STOP_STATE_OWNED:
            raise StopLifecycleError("new stop lifecycle must start in owned state")
        if candidate.stop_lifecycle_id in self._by_id:
            raise StopLifecycleConflictError("stop lifecycle ID belongs to another start lifecycle")
        self._by_start_lifecycle[candidate.start_lifecycle_id] = candidate
        self._by_id[candidate.stop_lifecycle_id] = candidate
        return StopLifecycleRecordResult(candidate, created=True, idempotent_replay=False)

    def update(self, record: ExecutionStopLifecycleRecord) -> ExecutionStopLifecycleRecord:
        candidate = record.validated()
        existing = self._by_id.get(candidate.stop_lifecycle_id)
        if existing is None:
            raise StopLifecycleError("stop lifecycle record not found")
        validate_stop_lifecycle_transition(existing, candidate)
        self._by_id[candidate.stop_lifecycle_id] = candidate
        self._by_start_lifecycle[candidate.start_lifecycle_id] = candidate
        return candidate

    def as_storage(self) -> dict[str, Any]:
        return {
            "schema_version": STOP_LIFECYCLE_SCHEMA_VERSION,
            "records": [record.as_dict() for record in self.records],
        }

    @classmethod
    def from_storage(cls, value: dict[str, Any] | None) -> "ExecutionStopLifecycleLedger":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise StopLifecycleError("stop lifecycle storage must be an object")
        if value.get("schema_version") != STOP_LIFECYCLE_SCHEMA_VERSION:
            raise StopLifecycleError("unsupported stop lifecycle storage schema")
        raw = value.get("records", [])
        if not isinstance(raw, list):
            raise StopLifecycleError("stop lifecycle records must be a list")
        records: list[ExecutionStopLifecycleRecord] = []
        for item in raw:
            if not isinstance(item, dict):
                raise StopLifecycleError("stop lifecycle record must be an object")
            records.append(ExecutionStopLifecycleRecord.from_dict(item))
        return cls(tuple(records))


class ExecutionStopLifecycleRepository:
    """Persistent stop lifecycle repository with atomic Store writes."""

    def __init__(self, store: StopLifecycleStore) -> None:
        self._store = store
        self._lock = asyncio.Lock()
        self._ledger: ExecutionStopLifecycleLedger | None = None

    async def _async_ledger(self) -> ExecutionStopLifecycleLedger:
        if self._ledger is None:
            self._ledger = ExecutionStopLifecycleLedger.from_storage(
                await self._store.async_load()
            )
        return self._ledger

    async def async_create_owned(
        self,
        record: ExecutionStopLifecycleRecord,
    ) -> StopLifecycleRecordResult:
        async with self._lock:
            current = await self._async_ledger()
            candidate = ExecutionStopLifecycleLedger(current.records)
            result = candidate.create_owned(record)
            if result.created:
                await self._store.async_save(candidate.as_storage())
                self._ledger = candidate
            return result

    async def async_update(
        self,
        record: ExecutionStopLifecycleRecord,
    ) -> ExecutionStopLifecycleRecord:
        async with self._lock:
            current = await self._async_ledger()
            candidate = ExecutionStopLifecycleLedger(current.records)
            updated = candidate.update(record)
            await self._store.async_save(candidate.as_storage())
            self._ledger = candidate
            return updated

    async def async_get_by_start_lifecycle_id(
        self,
        start_lifecycle_id: str,
    ) -> ExecutionStopLifecycleRecord | None:
        async with self._lock:
            return (await self._async_ledger()).get_by_start_lifecycle_id(start_lifecycle_id)

    async def async_list(self) -> tuple[ExecutionStopLifecycleRecord, ...]:
        async with self._lock:
            return (await self._async_ledger()).records


def home_assistant_stop_lifecycle_repository(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionStopLifecycleRepository:
    store = Store(
        hass,
        STOP_LIFECYCLE_STORAGE_VERSION,
        stop_lifecycle_storage_key(entry_id),
    )
    return ExecutionStopLifecycleRepository(store)
