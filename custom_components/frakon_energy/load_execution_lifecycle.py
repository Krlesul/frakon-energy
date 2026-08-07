"""Durable execution lifecycle state machine for FRAKON Energy.

This module persists the exact prepared plan and models crash-safe lifecycle
transitions. It contains no Home Assistant service call and exposes no executor.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, replace
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any, Protocol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .energy_load_planner import LoadPlan
from .load_execution_action_snapshot import ExecutionActionSnapshot
from .load_execution_attempt import ExecutionAttempt
from .load_execution_readiness import ExecutionReadinessDecision, READINESS_READY

LIFECYCLE_STORAGE_VERSION = 1
LIFECYCLE_SCHEMA_VERSION = 1

STATE_PREPARED = "prepared"
STATE_DISPATCHING = "dispatching"
STATE_DISPATCHED = "dispatched"
STATE_RECOVERY_REQUIRED = "recovery_required"
STATE_VERIFIED = "verified"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"
LIFECYCLE_STATES = (
    STATE_PREPARED,
    STATE_DISPATCHING,
    STATE_DISPATCHED,
    STATE_RECOVERY_REQUIRED,
    STATE_VERIFIED,
    STATE_FAILED,
    STATE_CANCELLED,
)

CALL_NOT_STARTED = "not_started"
CALL_UNKNOWN = "unknown"
CALL_CONFIRMED = "confirmed"
CALL_FAILED = "failed"
CALL_STATUSES = (CALL_NOT_STARTED, CALL_UNKNOWN, CALL_CONFIRMED, CALL_FAILED)

VERIFY_PENDING = "pending"
VERIFY_CONFIRMED = "confirmed"
VERIFY_FAILED = "failed"
VERIFY_STATUSES = (VERIFY_PENDING, VERIFY_CONFIRMED, VERIFY_FAILED)

_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class ExecutionLifecycleError(ValueError):
    """Raised for invalid lifecycle records or transitions."""


class ExecutionLifecycleConflictError(ExecutionLifecycleError):
    """Raised when an attempt is rebound to a different lifecycle identity."""


class LifecycleStore(Protocol):
    async def async_load(self) -> dict[str, Any] | None: ...
    async def async_save(self, data: dict[str, Any]) -> None: ...


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _aware_datetime(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as err:
        raise ExecutionLifecycleError(f"{field} must be an ISO-8601 datetime") from err
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExecutionLifecycleError(f"{field} must include a timezone offset")
    return parsed


def _finite(value: float, field: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ExecutionLifecycleError(f"{field} must be finite")
    return number


@dataclass(frozen=True, slots=True)
class ExecutionPlanSnapshot:
    """Durable exact plan payload used for restart-safe scheduling/revalidation."""

    load_id: str
    name: str
    starts_at: str
    ends_at: str
    duration_minutes: int
    interval_count: int
    power_kw: float
    average_czk_kwh: float
    minimum_czk_kwh: float
    maximum_czk_kwh: float
    estimated_energy_kwh: float
    estimated_cost_czk: float

    def validated(self) -> "ExecutionPlanSnapshot":
        if not self.load_id or not self.name:
            raise ExecutionLifecycleError("plan load_id and name are required")
        starts = _aware_datetime(self.starts_at, "plan.starts_at")
        ends = _aware_datetime(self.ends_at, "plan.ends_at")
        if ends <= starts:
            raise ExecutionLifecycleError("plan ends_at must be after starts_at")
        if self.duration_minutes <= 0 or self.duration_minutes % 15 != 0:
            raise ExecutionLifecycleError("plan duration must be a positive multiple of 15")
        if self.interval_count != self.duration_minutes // 15:
            raise ExecutionLifecycleError("plan interval_count does not match duration")
        if int((ends - starts).total_seconds()) != self.duration_minutes * 60:
            raise ExecutionLifecycleError("plan time window does not match duration")
        power = _finite(self.power_kw, "plan.power_kw")
        average = _finite(self.average_czk_kwh, "plan.average_czk_kwh")
        minimum = _finite(self.minimum_czk_kwh, "plan.minimum_czk_kwh")
        maximum = _finite(self.maximum_czk_kwh, "plan.maximum_czk_kwh")
        energy = _finite(self.estimated_energy_kwh, "plan.estimated_energy_kwh")
        cost = _finite(self.estimated_cost_czk, "plan.estimated_cost_czk")
        if power <= 0:
            raise ExecutionLifecycleError("plan power must be positive")
        if minimum > average or average > maximum:
            raise ExecutionLifecycleError("plan average price is outside min/max")
        expected_energy = power * self.duration_minutes / 60
        if not math.isclose(energy, expected_energy, rel_tol=1e-9, abs_tol=1e-9):
            raise ExecutionLifecycleError("plan energy is inconsistent")
        if not math.isclose(cost, energy * average, rel_tol=1e-9, abs_tol=1e-9):
            raise ExecutionLifecycleError("plan cost is inconsistent")
        return self

    @classmethod
    def from_load_plan(cls, plan: LoadPlan) -> "ExecutionPlanSnapshot":
        return cls(**plan.as_dict()).validated()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExecutionPlanSnapshot":
        try:
            return cls(
                load_id=str(value["load_id"]),
                name=str(value["name"]),
                starts_at=str(value["starts_at"]),
                ends_at=str(value["ends_at"]),
                duration_minutes=int(value["duration_minutes"]),
                interval_count=int(value["interval_count"]),
                power_kw=float(value["power_kw"]),
                average_czk_kwh=float(value["average_czk_kwh"]),
                minimum_czk_kwh=float(value["minimum_czk_kwh"]),
                maximum_czk_kwh=float(value["maximum_czk_kwh"]),
                estimated_energy_kwh=float(value["estimated_energy_kwh"]),
                estimated_cost_czk=float(value["estimated_cost_czk"]),
            ).validated()
        except (KeyError, TypeError, ValueError) as err:
            if isinstance(err, ExecutionLifecycleError):
                raise
            raise ExecutionLifecycleError("invalid persisted execution plan snapshot") from err

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_load_plan(self) -> LoadPlan:
        self.validated()
        return LoadPlan(**self.as_dict())

    def digest(self) -> str:
        self.validated()
        return hashlib.sha256(_canonical_json(self.as_dict())).hexdigest()


def lifecycle_storage_key(entry_id: str) -> str:
    if not entry_id:
        raise ExecutionLifecycleError("entry_id is required")
    digest = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:20]
    return f"{DOMAIN}.load_execution_lifecycle.{digest}"


def _lifecycle_id(
    *,
    attempt_id: str,
    action_snapshot_id: str,
    approval_snapshot_digest: str,
    plan_digest: str,
) -> str:
    payload = "\0".join((attempt_id, action_snapshot_id, approval_snapshot_digest, plan_digest))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class ExecutionLifecycleRecord:
    """Durable state machine record for one immutable execution attempt."""

    lifecycle_id: str
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
    state: str
    service_call_status: str
    verification_status: str
    created_at: int
    updated_at: int
    dispatch_attempts: int = 0
    dispatch_started_at: int | None = None
    dispatch_confirmed_at: int | None = None
    verified_at: int | None = None
    failed_at: int | None = None
    cancelled_at: int | None = None
    failure_reason: str | None = None
    executor_available: bool = False

    def validated(self) -> "ExecutionLifecycleRecord":
        if not _HEX_32.fullmatch(self.lifecycle_id):
            raise ExecutionLifecycleError("lifecycle_id must be a 32-character hex digest")
        if not self.entry_id or not self.attempt_id or not self.action_snapshot_id:
            raise ExecutionLifecycleError("entry/attempt/action snapshot identity is required")
        if not self.profile_id or not self.entity_id:
            raise ExecutionLifecycleError("profile_id and entity_id are required")
        if not _HEX_64.fullmatch(self.approval_snapshot_digest):
            raise ExecutionLifecycleError("approval_snapshot_digest must be SHA-256 hex")
        if not _HEX_64.fullmatch(self.plan_digest):
            raise ExecutionLifecycleError("plan_digest must be SHA-256 hex")
        plan = self.plan.validated()
        if plan.digest() != self.plan_digest:
            raise ExecutionLifecycleError("persisted plan digest does not match plan payload")
        expected_id = _lifecycle_id(
            attempt_id=self.attempt_id,
            action_snapshot_id=self.action_snapshot_id,
            approval_snapshot_digest=self.approval_snapshot_digest,
            plan_digest=self.plan_digest,
        )
        if self.lifecycle_id != expected_id:
            raise ExecutionLifecycleError("lifecycle identity does not match immutable binding")
        if self.state not in LIFECYCLE_STATES:
            raise ExecutionLifecycleError(f"unsupported lifecycle state: {self.state}")
        if self.service_call_status not in CALL_STATUSES:
            raise ExecutionLifecycleError("unsupported service_call_status")
        if self.verification_status not in VERIFY_STATUSES:
            raise ExecutionLifecycleError("unsupported verification_status")
        if self.created_at < 0 or self.updated_at < self.created_at:
            raise ExecutionLifecycleError("lifecycle timestamps are invalid")
        if self.dispatch_attempts < 0:
            raise ExecutionLifecycleError("dispatch_attempts cannot be negative")
        if self.executor_available:
            raise ExecutionLifecycleError("lifecycle model cannot advertise an executor")

        if self.state == STATE_PREPARED:
            if self.service_call_status != CALL_NOT_STARTED or self.verification_status != VERIFY_PENDING:
                raise ExecutionLifecycleError("prepared lifecycle has invalid call/verification status")
            if self.dispatch_attempts != 0 or self.dispatch_started_at is not None:
                raise ExecutionLifecycleError("prepared lifecycle cannot contain dispatch evidence")
        elif self.state == STATE_DISPATCHING:
            if self.service_call_status != CALL_UNKNOWN or self.verification_status != VERIFY_PENDING:
                raise ExecutionLifecycleError("dispatching lifecycle must have unknown call status")
            if self.dispatch_attempts < 1 or self.dispatch_started_at is None:
                raise ExecutionLifecycleError("dispatching lifecycle requires dispatch evidence")
        elif self.state == STATE_DISPATCHED:
            if self.service_call_status != CALL_CONFIRMED or self.verification_status != VERIFY_PENDING:
                raise ExecutionLifecycleError("dispatched lifecycle has invalid status")
            if self.dispatch_confirmed_at is None or self.dispatch_started_at is None:
                raise ExecutionLifecycleError("dispatched lifecycle requires dispatch timestamps")
        elif self.state == STATE_RECOVERY_REQUIRED:
            if self.service_call_status != CALL_UNKNOWN or self.verification_status != VERIFY_PENDING:
                raise ExecutionLifecycleError("recovery lifecycle must preserve unknown call status")
            if self.dispatch_started_at is None:
                raise ExecutionLifecycleError("recovery lifecycle requires prior dispatch start")
        elif self.state == STATE_VERIFIED:
            if self.verification_status != VERIFY_CONFIRMED or self.verified_at is None:
                raise ExecutionLifecycleError("verified lifecycle requires verification evidence")
            if self.service_call_status not in (CALL_CONFIRMED, CALL_UNKNOWN):
                raise ExecutionLifecycleError("verified lifecycle has impossible call status")
        elif self.state == STATE_FAILED:
            if self.verification_status != VERIFY_FAILED or self.failed_at is None or not self.failure_reason:
                raise ExecutionLifecycleError("failed lifecycle requires failure evidence")
        elif self.state == STATE_CANCELLED:
            if self.service_call_status != CALL_NOT_STARTED or self.cancelled_at is None:
                raise ExecutionLifecycleError("cancelled lifecycle must predate dispatch")
            if self.dispatch_attempts != 0:
                raise ExecutionLifecycleError("cancelled lifecycle cannot contain dispatch attempts")
        return self

    @classmethod
    def prepared(
        cls,
        *,
        attempt: ExecutionAttempt,
        action_snapshot: ExecutionActionSnapshot,
        plan: LoadPlan,
        readiness: ExecutionReadinessDecision,
        created_at: int,
    ) -> "ExecutionLifecycleRecord":
        attempt.validated()
        action_snapshot.validated()
        if readiness.status != READINESS_READY or not readiness.action_required:
            raise ExecutionLifecycleError("execution readiness must be ready with an action required")
        if readiness.executor_available or readiness.execution_performed or readiness.service_call_performed:
            raise ExecutionLifecycleError("readiness result contains impossible execution evidence")
        if attempt.attempt_id != action_snapshot.attempt_id:
            raise ExecutionLifecycleError("attempt and action snapshot do not match")
        if attempt.entry_id != action_snapshot.entry_id or attempt.profile_id != action_snapshot.profile_id:
            raise ExecutionLifecycleError("attempt/action snapshot scope mismatch")
        if attempt.entity_id is None or attempt.entity_id != action_snapshot.entity_id:
            raise ExecutionLifecycleError("attempt/action snapshot entity mismatch")
        if attempt.snapshot_digest != action_snapshot.approval_snapshot_digest:
            raise ExecutionLifecycleError("attempt/action snapshot approval scope mismatch")
        if readiness.attempt_id != attempt.attempt_id or readiness.action_snapshot_id != action_snapshot.snapshot_id:
            raise ExecutionLifecycleError("readiness identity does not match audit records")
        if readiness.profile_id != attempt.profile_id or readiness.entity_id != action_snapshot.entity_id:
            raise ExecutionLifecycleError("readiness scope does not match audit records")
        if readiness.plan_starts_at != plan.starts_at or readiness.plan_ends_at != plan.ends_at:
            raise ExecutionLifecycleError("readiness timing does not match plan")
        if not readiness.approval_scope_matches or not readiness.policy_eligible:
            raise ExecutionLifecycleError("readiness scope/policy is not eligible")
        if not readiness.attempt_matches or not readiness.profile_matches:
            raise ExecutionLifecycleError("readiness audit binding is not valid")
        if created_at < attempt.created_at:
            raise ExecutionLifecycleError("lifecycle cannot predate execution attempt")

        plan_snapshot = ExecutionPlanSnapshot.from_load_plan(plan)
        plan_digest = plan_snapshot.digest()
        lifecycle_id = _lifecycle_id(
            attempt_id=attempt.attempt_id,
            action_snapshot_id=action_snapshot.snapshot_id,
            approval_snapshot_digest=attempt.snapshot_digest,
            plan_digest=plan_digest,
        )
        return cls(
            lifecycle_id=lifecycle_id,
            entry_id=attempt.entry_id,
            attempt_id=attempt.attempt_id,
            action_snapshot_id=action_snapshot.snapshot_id,
            profile_id=attempt.profile_id,
            entity_id=action_snapshot.entity_id,
            approval_snapshot_digest=attempt.snapshot_digest,
            plan_digest=plan_digest,
            plan=plan_snapshot,
            service_domain=action_snapshot.service_domain,
            service_name=action_snapshot.service_name,
            desired_state=action_snapshot.desired_state,
            state=STATE_PREPARED,
            service_call_status=CALL_NOT_STARTED,
            verification_status=VERIFY_PENDING,
            created_at=created_at,
            updated_at=created_at,
            executor_available=False,
        ).validated()

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["plan"] = self.plan.as_dict()
        result["service_call_performed"] = (
            True if self.service_call_status == CALL_CONFIRMED
            else None if self.service_call_status == CALL_UNKNOWN
            else False
        )
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExecutionLifecycleRecord":
        try:
            raw_plan = value["plan"]
            if not isinstance(raw_plan, dict):
                raise ExecutionLifecycleError("persisted lifecycle plan must be an object")
            return cls(
                lifecycle_id=str(value["lifecycle_id"]),
                entry_id=str(value["entry_id"]),
                attempt_id=str(value["attempt_id"]),
                action_snapshot_id=str(value["action_snapshot_id"]),
                profile_id=str(value["profile_id"]),
                entity_id=str(value["entity_id"]),
                approval_snapshot_digest=str(value["approval_snapshot_digest"]),
                plan_digest=str(value["plan_digest"]),
                plan=ExecutionPlanSnapshot.from_dict(raw_plan),
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
                failed_at=int(value["failed_at"]) if value.get("failed_at") is not None else None,
                cancelled_at=int(value["cancelled_at"]) if value.get("cancelled_at") is not None else None,
                failure_reason=str(value["failure_reason"]) if value.get("failure_reason") is not None else None,
                executor_available=bool(value.get("executor_available", False)),
            ).validated()
        except (KeyError, TypeError, ValueError) as err:
            if isinstance(err, ExecutionLifecycleError):
                raise
            raise ExecutionLifecycleError("invalid persisted lifecycle record") from err


def _transition_time(record: ExecutionLifecycleRecord, now: int) -> int:
    if now < record.updated_at:
        raise ExecutionLifecycleError("lifecycle transition timestamp cannot move backwards")
    return now


def begin_dispatch(record: ExecutionLifecycleRecord, *, now: int) -> ExecutionLifecycleRecord:
    """Persist this state before a future executor makes a service call."""
    record.validated()
    if record.state not in (STATE_PREPARED, STATE_RECOVERY_REQUIRED):
        raise ExecutionLifecycleError(f"cannot begin dispatch from state {record.state}")
    timestamp = _transition_time(record, now)
    return replace(
        record,
        state=STATE_DISPATCHING,
        service_call_status=CALL_UNKNOWN,
        verification_status=VERIFY_PENDING,
        updated_at=timestamp,
        dispatch_attempts=record.dispatch_attempts + 1,
        dispatch_started_at=timestamp,
        dispatch_confirmed_at=None,
        failure_reason=None,
    ).validated()


def confirm_dispatch(record: ExecutionLifecycleRecord, *, now: int) -> ExecutionLifecycleRecord:
    """Record a future executor's confirmed service-call return, without verification."""
    record.validated()
    if record.state != STATE_DISPATCHING:
        raise ExecutionLifecycleError(f"cannot confirm dispatch from state {record.state}")
    timestamp = _transition_time(record, now)
    return replace(
        record,
        state=STATE_DISPATCHED,
        service_call_status=CALL_CONFIRMED,
        updated_at=timestamp,
        dispatch_confirmed_at=timestamp,
    ).validated()


def require_recovery_after_restart(
    record: ExecutionLifecycleRecord,
    *,
    now: int,
) -> ExecutionLifecycleRecord:
    """Convert an interrupted dispatch into an explicit unknown-outcome state."""
    record.validated()
    if record.state != STATE_DISPATCHING:
        return record
    timestamp = _transition_time(record, now)
    return replace(
        record,
        state=STATE_RECOVERY_REQUIRED,
        service_call_status=CALL_UNKNOWN,
        updated_at=timestamp,
    ).validated()


def verify_desired_state(
    record: ExecutionLifecycleRecord,
    *,
    current_state: str | None,
    now: int,
) -> ExecutionLifecycleRecord:
    """Verify a dispatched/recovery action by observed entity state."""
    record.validated()
    if record.state not in (STATE_DISPATCHED, STATE_RECOVERY_REQUIRED):
        raise ExecutionLifecycleError(f"cannot verify from state {record.state}")
    normalized = current_state.strip().lower() if isinstance(current_state, str) else None
    if normalized != record.desired_state:
        raise ExecutionLifecycleError("current entity state does not match desired state")
    timestamp = _transition_time(record, now)
    return replace(
        record,
        state=STATE_VERIFIED,
        verification_status=VERIFY_CONFIRMED,
        updated_at=timestamp,
        verified_at=timestamp,
    ).validated()


def mark_failed(
    record: ExecutionLifecycleRecord,
    *,
    reason: str,
    now: int,
    service_call_status: str | None = None,
) -> ExecutionLifecycleRecord:
    record.validated()
    if record.state in (STATE_VERIFIED, STATE_FAILED, STATE_CANCELLED):
        raise ExecutionLifecycleError(f"cannot fail terminal state {record.state}")
    if not reason.strip():
        raise ExecutionLifecycleError("failure reason is required")
    timestamp = _transition_time(record, now)
    call_status = service_call_status or record.service_call_status
    if call_status not in CALL_STATUSES:
        raise ExecutionLifecycleError("invalid service call status for failure")
    return replace(
        record,
        state=STATE_FAILED,
        service_call_status=call_status,
        verification_status=VERIFY_FAILED,
        updated_at=timestamp,
        failed_at=timestamp,
        failure_reason=reason.strip(),
    ).validated()


def cancel_prepared(record: ExecutionLifecycleRecord, *, now: int) -> ExecutionLifecycleRecord:
    record.validated()
    if record.state != STATE_PREPARED:
        raise ExecutionLifecycleError(f"cannot cancel state {record.state}")
    timestamp = _transition_time(record, now)
    return replace(
        record,
        state=STATE_CANCELLED,
        updated_at=timestamp,
        cancelled_at=timestamp,
    ).validated()


_ALLOWED_TRANSITIONS = {
    STATE_PREPARED: {STATE_DISPATCHING, STATE_FAILED, STATE_CANCELLED},
    STATE_DISPATCHING: {STATE_DISPATCHED, STATE_RECOVERY_REQUIRED, STATE_FAILED},
    STATE_RECOVERY_REQUIRED: {STATE_DISPATCHING, STATE_VERIFIED, STATE_FAILED},
    STATE_DISPATCHED: {STATE_VERIFIED, STATE_FAILED},
    STATE_VERIFIED: set(),
    STATE_FAILED: set(),
    STATE_CANCELLED: set(),
}


def validate_lifecycle_transition(
    previous: ExecutionLifecycleRecord,
    updated: ExecutionLifecycleRecord,
) -> None:
    previous.validated()
    updated.validated()
    immutable_fields = (
        "lifecycle_id",
        "entry_id",
        "attempt_id",
        "action_snapshot_id",
        "profile_id",
        "entity_id",
        "approval_snapshot_digest",
        "plan_digest",
        "plan",
        "service_domain",
        "service_name",
        "desired_state",
        "created_at",
    )
    if any(getattr(previous, field) != getattr(updated, field) for field in immutable_fields):
        raise ExecutionLifecycleConflictError("lifecycle immutable binding changed")
    if updated.state == previous.state:
        if updated != previous:
            raise ExecutionLifecycleError("same-state lifecycle update is not allowed")
        return
    if updated.state not in _ALLOWED_TRANSITIONS[previous.state]:
        raise ExecutionLifecycleError(
            f"invalid lifecycle transition: {previous.state} -> {updated.state}"
        )


@dataclass(frozen=True, slots=True)
class LifecycleRecordResult:
    record: ExecutionLifecycleRecord
    created: bool
    idempotent_replay: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "lifecycle": self.record.as_dict(),
            "created": self.created,
            "idempotent_replay": self.idempotent_replay,
            "executor_available": False,
        }


class ExecutionLifecycleLedger:
    def __init__(self, records: tuple[ExecutionLifecycleRecord, ...] = ()) -> None:
        self._by_attempt: dict[str, ExecutionLifecycleRecord] = {}
        self._by_id: dict[str, ExecutionLifecycleRecord] = {}
        for record in records:
            validated = record.validated()
            if validated.attempt_id in self._by_attempt:
                raise ExecutionLifecycleError(
                    f"duplicate lifecycle for attempt: {validated.attempt_id}"
                )
            if validated.lifecycle_id in self._by_id:
                raise ExecutionLifecycleError(
                    f"duplicate lifecycle id: {validated.lifecycle_id}"
                )
            self._by_attempt[validated.attempt_id] = validated
            self._by_id[validated.lifecycle_id] = validated

    @property
    def records(self) -> tuple[ExecutionLifecycleRecord, ...]:
        return tuple(sorted(self._by_id.values(), key=lambda item: (item.created_at, item.lifecycle_id)))

    def get_by_attempt_id(self, attempt_id: str) -> ExecutionLifecycleRecord | None:
        return self._by_attempt.get(attempt_id)

    def prepare(self, record: ExecutionLifecycleRecord) -> LifecycleRecordResult:
        candidate = record.validated()
        existing = self._by_attempt.get(candidate.attempt_id)
        if existing is not None:
            if existing.lifecycle_id == candidate.lifecycle_id:
                return LifecycleRecordResult(existing, created=False, idempotent_replay=True)
            raise ExecutionLifecycleConflictError(
                "execution attempt already has a lifecycle with different immutable binding"
            )
        if candidate.state != STATE_PREPARED:
            raise ExecutionLifecycleError("new lifecycle must start in prepared state")
        self._by_attempt[candidate.attempt_id] = candidate
        self._by_id[candidate.lifecycle_id] = candidate
        return LifecycleRecordResult(candidate, created=True, idempotent_replay=False)

    def update(self, record: ExecutionLifecycleRecord) -> ExecutionLifecycleRecord:
        candidate = record.validated()
        existing = self._by_id.get(candidate.lifecycle_id)
        if existing is None:
            raise ExecutionLifecycleError("lifecycle record not found")
        validate_lifecycle_transition(existing, candidate)
        self._by_id[candidate.lifecycle_id] = candidate
        self._by_attempt[candidate.attempt_id] = candidate
        return candidate

    def as_storage(self) -> dict[str, Any]:
        return {
            "schema_version": LIFECYCLE_SCHEMA_VERSION,
            "records": [record.as_dict() for record in self.records],
        }

    @classmethod
    def from_storage(cls, value: dict[str, Any] | None) -> "ExecutionLifecycleLedger":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ExecutionLifecycleError("lifecycle storage must be an object")
        if value.get("schema_version") != LIFECYCLE_SCHEMA_VERSION:
            raise ExecutionLifecycleError("unsupported lifecycle storage schema")
        raw_records = value.get("records", [])
        if not isinstance(raw_records, list):
            raise ExecutionLifecycleError("lifecycle records must be a list")
        records: list[ExecutionLifecycleRecord] = []
        for raw in raw_records:
            if not isinstance(raw, dict):
                raise ExecutionLifecycleError("lifecycle record must be an object")
            records.append(ExecutionLifecycleRecord.from_dict(raw))
        return cls(tuple(records))


class ExecutionLifecycleRepository:
    """Persistent lifecycle repository with idempotent prepare and atomic Store writes."""

    def __init__(self, store: LifecycleStore) -> None:
        self._store = store
        self._lock = asyncio.Lock()
        self._ledger: ExecutionLifecycleLedger | None = None

    async def _async_ledger(self) -> ExecutionLifecycleLedger:
        if self._ledger is None:
            self._ledger = ExecutionLifecycleLedger.from_storage(await self._store.async_load())
        return self._ledger

    async def async_prepare(self, record: ExecutionLifecycleRecord) -> LifecycleRecordResult:
        async with self._lock:
            current = await self._async_ledger()
            candidate = ExecutionLifecycleLedger(current.records)
            result = candidate.prepare(record)
            if result.created:
                await self._store.async_save(candidate.as_storage())
                self._ledger = candidate
            return result

    async def async_update(self, record: ExecutionLifecycleRecord) -> ExecutionLifecycleRecord:
        async with self._lock:
            current = await self._async_ledger()
            candidate = ExecutionLifecycleLedger(current.records)
            updated = candidate.update(record)
            await self._store.async_save(candidate.as_storage())
            self._ledger = candidate
            return updated

    async def async_get_by_attempt_id(self, attempt_id: str) -> ExecutionLifecycleRecord | None:
        async with self._lock:
            return (await self._async_ledger()).get_by_attempt_id(attempt_id)

    async def async_list(self) -> tuple[ExecutionLifecycleRecord, ...]:
        async with self._lock:
            return (await self._async_ledger()).records


def home_assistant_lifecycle_repository(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionLifecycleRepository:
    store = Store(hass, LIFECYCLE_STORAGE_VERSION, lifecycle_storage_key(entry_id))
    return ExecutionLifecycleRepository(store)
