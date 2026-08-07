"""Durable pre-start execution scheduling for FRAKON Energy.

A schedule persists the exact approved plan before the final start window opens.
It contains no executor and never calls a Home Assistant service. The later
execution lifecycle stays separate and may be prepared only after a fresh
readiness check.
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
from .energy_load_planner import LoadPlan
from .load_execution_action_snapshot import ExecutionActionSnapshot
from .load_execution_attempt import ExecutionAttempt
from .load_execution_lifecycle import ExecutionPlanSnapshot
from .load_execution_readiness import (
    READINESS_READY,
    READINESS_WAITING,
    ExecutionReadinessDecision,
)

SCHEDULE_STORAGE_VERSION = 1
SCHEDULE_SCHEMA_VERSION = 1
SCHEDULE_ALLOWED_READINESS = (READINESS_WAITING, READINESS_READY)
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class ExecutionScheduleError(ValueError):
    """Raised when an immutable execution schedule is invalid."""


class ExecutionScheduleConflictError(ExecutionScheduleError):
    """Raised when an attempt is rebound to a different scheduled plan."""


class ScheduleStore(Protocol):
    async def async_load(self) -> dict[str, Any] | None: ...
    async def async_save(self, data: dict[str, Any]) -> None: ...


def schedule_storage_key(entry_id: str) -> str:
    """Return a Home Assistant storage key isolated per config entry."""
    if not entry_id:
        raise ExecutionScheduleError("entry_id is required")
    digest = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:20]
    return f"{DOMAIN}.load_execution_schedules.{digest}"


def _schedule_id_from_fields(
    *,
    entry_id: str,
    attempt_id: str,
    action_snapshot_id: str,
    profile_id: str,
    entity_id: str,
    approval_id: str,
    approval_fingerprint: str,
    approval_snapshot_digest: str,
    plan_digest: str,
    service_domain: str,
    service_name: str,
    desired_state: str,
) -> str:
    payload = "\0".join(
        (
            entry_id,
            attempt_id,
            action_snapshot_id,
            profile_id,
            entity_id,
            approval_id,
            approval_fingerprint,
            approval_snapshot_digest,
            plan_digest,
            service_domain,
            service_name,
            desired_state,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class ExecutionSchedule:
    """Immutable durable schedule created from an approved waiting/ready plan."""

    schedule_id: str
    entry_id: str
    attempt_id: str
    action_snapshot_id: str
    profile_id: str
    entity_id: str
    approval_id: str
    approval_fingerprint: str
    approval_snapshot_digest: str
    plan_digest: str
    plan: ExecutionPlanSnapshot
    service_domain: str
    service_name: str
    desired_state: str
    created_at: int
    created_from_readiness: str
    execution_performed: bool = False
    service_call_performed: bool = False
    executor_available: bool = False

    def validated(self) -> "ExecutionSchedule":
        if not _HEX_32.fullmatch(self.schedule_id):
            raise ExecutionScheduleError("schedule_id must be a 32-character hex digest")
        if not self.entry_id or not self.attempt_id or not self.action_snapshot_id:
            raise ExecutionScheduleError("entry/attempt/action snapshot identity is required")
        if not self.profile_id or not self.entity_id or not self.approval_id:
            raise ExecutionScheduleError("profile/entity/approval identity is required")
        if not _HEX_64.fullmatch(self.approval_fingerprint):
            raise ExecutionScheduleError("approval_fingerprint must be SHA-256 hex")
        if not _HEX_64.fullmatch(self.approval_snapshot_digest):
            raise ExecutionScheduleError("approval_snapshot_digest must be SHA-256 hex")
        if not _HEX_64.fullmatch(self.plan_digest):
            raise ExecutionScheduleError("plan_digest must be SHA-256 hex")
        plan = self.plan.validated()
        if plan.load_id != self.profile_id:
            raise ExecutionScheduleError("scheduled plan load_id does not match profile_id")
        if plan.digest() != self.plan_digest:
            raise ExecutionScheduleError("scheduled plan digest does not match plan payload")
        if not self.service_domain or not self.service_name or not self.desired_state:
            raise ExecutionScheduleError("fixed service mapping is required")
        if self.created_at < 0:
            raise ExecutionScheduleError("created_at must be non-negative")
        if self.created_from_readiness not in SCHEDULE_ALLOWED_READINESS:
            raise ExecutionScheduleError("schedule must originate from waiting or ready readiness")
        expected_id = _schedule_id_from_fields(
            entry_id=self.entry_id,
            attempt_id=self.attempt_id,
            action_snapshot_id=self.action_snapshot_id,
            profile_id=self.profile_id,
            entity_id=self.entity_id,
            approval_id=self.approval_id,
            approval_fingerprint=self.approval_fingerprint,
            approval_snapshot_digest=self.approval_snapshot_digest,
            plan_digest=self.plan_digest,
            service_domain=self.service_domain,
            service_name=self.service_name,
            desired_state=self.desired_state,
        )
        if self.schedule_id != expected_id:
            raise ExecutionScheduleError("schedule identity does not match immutable binding")
        if self.execution_performed or self.service_call_performed:
            raise ExecutionScheduleError("schedule cannot represent performed execution")
        if self.executor_available:
            raise ExecutionScheduleError("schedule cannot advertise an executor")
        return self

    @classmethod
    def from_approved_readiness(
        cls,
        *,
        attempt: ExecutionAttempt,
        action_snapshot: ExecutionActionSnapshot,
        plan: LoadPlan,
        readiness: ExecutionReadinessDecision,
        created_at: int,
    ) -> "ExecutionSchedule":
        """Create an immutable schedule only from a clean waiting/ready readiness gate."""
        attempt.validated()
        action_snapshot.validated()
        if readiness.status not in SCHEDULE_ALLOWED_READINESS:
            raise ExecutionScheduleError(
                f"readiness cannot be scheduled: {readiness.status}/{readiness.reason}"
            )
        if readiness.status == READINESS_WAITING and readiness.action_required:
            raise ExecutionScheduleError("waiting readiness cannot require an action yet")
        if readiness.status == READINESS_READY and not readiness.action_required:
            raise ExecutionScheduleError("ready readiness must require an action")
        if (
            readiness.execution_performed
            or readiness.service_call_performed
            or readiness.executor_available
        ):
            raise ExecutionScheduleError("readiness contains impossible execution evidence")
        if not readiness.approval_scope_matches or not readiness.policy_eligible:
            raise ExecutionScheduleError("readiness approval scope/policy is not eligible")
        if not readiness.attempt_matches or not readiness.profile_matches:
            raise ExecutionScheduleError("readiness audit binding is not valid")
        if attempt.attempt_id != action_snapshot.attempt_id:
            raise ExecutionScheduleError("attempt and action snapshot do not match")
        if attempt.entry_id != action_snapshot.entry_id:
            raise ExecutionScheduleError("attempt and action snapshot entry do not match")
        if attempt.profile_id != action_snapshot.profile_id:
            raise ExecutionScheduleError("attempt and action snapshot profile do not match")
        if attempt.entity_id is None or attempt.entity_id != action_snapshot.entity_id:
            raise ExecutionScheduleError("attempt and action snapshot entity do not match")
        if attempt.approval_id != action_snapshot.approval_id:
            raise ExecutionScheduleError("attempt and action snapshot approval do not match")
        if attempt.approval_fingerprint != action_snapshot.approval_fingerprint:
            raise ExecutionScheduleError("attempt and action snapshot approval fingerprint changed")
        if attempt.snapshot_digest != action_snapshot.approval_snapshot_digest:
            raise ExecutionScheduleError("attempt and action snapshot approval scope changed")
        if readiness.attempt_id != attempt.attempt_id:
            raise ExecutionScheduleError("readiness attempt identity changed")
        if readiness.action_snapshot_id != action_snapshot.snapshot_id:
            raise ExecutionScheduleError("readiness action snapshot identity changed")
        if readiness.profile_id != attempt.profile_id or readiness.entity_id != action_snapshot.entity_id:
            raise ExecutionScheduleError("readiness profile/entity scope changed")
        if readiness.plan_starts_at != plan.starts_at or readiness.plan_ends_at != plan.ends_at:
            raise ExecutionScheduleError("readiness timing does not match scheduled plan")
        if created_at < attempt.created_at:
            raise ExecutionScheduleError("schedule cannot predate execution attempt")

        plan_snapshot = ExecutionPlanSnapshot.from_load_plan(plan)
        plan_digest = plan_snapshot.digest()
        schedule_id = _schedule_id_from_fields(
            entry_id=attempt.entry_id,
            attempt_id=attempt.attempt_id,
            action_snapshot_id=action_snapshot.snapshot_id,
            profile_id=attempt.profile_id,
            entity_id=action_snapshot.entity_id,
            approval_id=attempt.approval_id,
            approval_fingerprint=attempt.approval_fingerprint,
            approval_snapshot_digest=attempt.snapshot_digest,
            plan_digest=plan_digest,
            service_domain=action_snapshot.service_domain,
            service_name=action_snapshot.service_name,
            desired_state=action_snapshot.desired_state,
        )
        return cls(
            schedule_id=schedule_id,
            entry_id=attempt.entry_id,
            attempt_id=attempt.attempt_id,
            action_snapshot_id=action_snapshot.snapshot_id,
            profile_id=attempt.profile_id,
            entity_id=action_snapshot.entity_id,
            approval_id=attempt.approval_id,
            approval_fingerprint=attempt.approval_fingerprint,
            approval_snapshot_digest=attempt.snapshot_digest,
            plan_digest=plan_digest,
            plan=plan_snapshot,
            service_domain=action_snapshot.service_domain,
            service_name=action_snapshot.service_name,
            desired_state=action_snapshot.desired_state,
            created_at=created_at,
            created_from_readiness=readiness.status,
        ).validated()

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["plan"] = self.plan.as_dict()
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExecutionSchedule":
        try:
            raw_plan = value["plan"]
            if not isinstance(raw_plan, dict):
                raise ExecutionScheduleError("persisted schedule plan must be an object")
            return cls(
                schedule_id=str(value["schedule_id"]),
                entry_id=str(value["entry_id"]),
                attempt_id=str(value["attempt_id"]),
                action_snapshot_id=str(value["action_snapshot_id"]),
                profile_id=str(value["profile_id"]),
                entity_id=str(value["entity_id"]),
                approval_id=str(value["approval_id"]),
                approval_fingerprint=str(value["approval_fingerprint"]),
                approval_snapshot_digest=str(value["approval_snapshot_digest"]),
                plan_digest=str(value["plan_digest"]),
                plan=ExecutionPlanSnapshot.from_dict(raw_plan),
                service_domain=str(value["service_domain"]),
                service_name=str(value["service_name"]),
                desired_state=str(value["desired_state"]),
                created_at=int(value["created_at"]),
                created_from_readiness=str(value["created_from_readiness"]),
                execution_performed=bool(value.get("execution_performed", False)),
                service_call_performed=bool(value.get("service_call_performed", False)),
                executor_available=bool(value.get("executor_available", False)),
            ).validated()
        except (KeyError, TypeError, ValueError) as err:
            if isinstance(err, ExecutionScheduleError):
                raise
            raise ExecutionScheduleError("invalid persisted execution schedule") from err


@dataclass(frozen=True, slots=True)
class ScheduleRecordResult:
    schedule: ExecutionSchedule
    created: bool
    idempotent_replay: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "schedule": self.schedule.as_dict(),
            "created": self.created,
            "idempotent_replay": self.idempotent_replay,
            "execution_performed": False,
            "service_call_performed": False,
            "executor_available": False,
        }


class ExecutionScheduleLedger:
    """Immutable schedule ledger keyed by consumed execution attempt."""

    def __init__(self, schedules: tuple[ExecutionSchedule, ...] = ()) -> None:
        self._by_attempt: dict[str, ExecutionSchedule] = {}
        self._by_id: dict[str, ExecutionSchedule] = {}
        for schedule in schedules:
            validated = schedule.validated()
            if validated.attempt_id in self._by_attempt:
                raise ExecutionScheduleError(
                    f"duplicate schedule for attempt: {validated.attempt_id}"
                )
            if validated.schedule_id in self._by_id:
                raise ExecutionScheduleError(
                    f"duplicate schedule id: {validated.schedule_id}"
                )
            self._by_attempt[validated.attempt_id] = validated
            self._by_id[validated.schedule_id] = validated

    @property
    def schedules(self) -> tuple[ExecutionSchedule, ...]:
        return tuple(
            sorted(self._by_id.values(), key=lambda item: (item.created_at, item.schedule_id))
        )

    def get_by_attempt_id(self, attempt_id: str) -> ExecutionSchedule | None:
        return self._by_attempt.get(attempt_id)

    def get_by_schedule_id(self, schedule_id: str) -> ExecutionSchedule | None:
        return self._by_id.get(schedule_id)

    def record(self, schedule: ExecutionSchedule) -> ScheduleRecordResult:
        candidate = schedule.validated()
        existing = self._by_attempt.get(candidate.attempt_id)
        if existing is not None:
            if existing.schedule_id == candidate.schedule_id:
                return ScheduleRecordResult(existing, created=False, idempotent_replay=True)
            raise ExecutionScheduleConflictError(
                "execution attempt already has a different immutable schedule"
            )
        if candidate.schedule_id in self._by_id:
            raise ExecutionScheduleConflictError(
                "schedule_id already belongs to a different execution attempt"
            )
        self._by_attempt[candidate.attempt_id] = candidate
        self._by_id[candidate.schedule_id] = candidate
        return ScheduleRecordResult(candidate, created=True, idempotent_replay=False)

    def as_storage(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEDULE_SCHEMA_VERSION,
            "schedules": [schedule.as_dict() for schedule in self.schedules],
        }

    @classmethod
    def from_storage(cls, value: dict[str, Any] | None) -> "ExecutionScheduleLedger":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ExecutionScheduleError("schedule storage must be an object")
        if value.get("schema_version") != SCHEDULE_SCHEMA_VERSION:
            raise ExecutionScheduleError("unsupported schedule storage schema")
        raw_schedules = value.get("schedules", [])
        if not isinstance(raw_schedules, list):
            raise ExecutionScheduleError("schedules must be a list")
        schedules: list[ExecutionSchedule] = []
        for raw in raw_schedules:
            if not isinstance(raw, dict):
                raise ExecutionScheduleError("schedule record must be an object")
            schedules.append(ExecutionSchedule.from_dict(raw))
        return cls(tuple(schedules))


class ExecutionScheduleRepository:
    """Persistent idempotent schedule repository with Store rollback semantics."""

    def __init__(self, store: ScheduleStore) -> None:
        self._store = store
        self._lock = asyncio.Lock()
        self._ledger: ExecutionScheduleLedger | None = None

    async def _async_ledger(self) -> ExecutionScheduleLedger:
        if self._ledger is None:
            self._ledger = ExecutionScheduleLedger.from_storage(await self._store.async_load())
        return self._ledger

    async def async_record(self, schedule: ExecutionSchedule) -> ScheduleRecordResult:
        async with self._lock:
            current = await self._async_ledger()
            candidate = ExecutionScheduleLedger(current.schedules)
            result = candidate.record(schedule)
            if result.created:
                await self._store.async_save(candidate.as_storage())
                self._ledger = candidate
            return result

    async def async_get_by_attempt_id(self, attempt_id: str) -> ExecutionSchedule | None:
        async with self._lock:
            return (await self._async_ledger()).get_by_attempt_id(attempt_id)

    async def async_get_by_schedule_id(self, schedule_id: str) -> ExecutionSchedule | None:
        async with self._lock:
            return (await self._async_ledger()).get_by_schedule_id(schedule_id)

    async def async_list(self) -> tuple[ExecutionSchedule, ...]:
        async with self._lock:
            return (await self._async_ledger()).schedules


def home_assistant_schedule_repository(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionScheduleRepository:
    store = Store(hass, SCHEDULE_STORAGE_VERSION, schedule_storage_key(entry_id))
    return ExecutionScheduleRepository(store)
