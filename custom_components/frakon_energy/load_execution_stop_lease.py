"""Durable stop obligations for bounded FRAKON Energy load execution.

A stop lease is inert audit/safety state. It is persisted before any future
start dispatch so a later executor cannot start a bounded load without first
recording the exact allowlisted turn-off obligation and plan end time.
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
from .load_execution_lifecycle import STATE_PREPARED, ExecutionLifecycleRecord

STOP_LEASE_STORAGE_VERSION = 1
STOP_LEASE_SCHEMA_VERSION = 1
STOP_LEASE_ARMED = "armed"

_STOP_SERVICE_BY_START: dict[tuple[str, str], tuple[str, str]] = {
    ("switch", "turn_on"): ("switch", "turn_off"),
    ("input_boolean", "turn_on"): ("input_boolean", "turn_off"),
}
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class StopLeaseError(ValueError):
    """Raised when a durable stop obligation is invalid."""


class StopLeaseConflictError(StopLeaseError):
    """Raised when one lifecycle is rebound to a different stop obligation."""


class StopLeaseStore(Protocol):
    async def async_load(self) -> dict[str, Any] | None: ...
    async def async_save(self, data: dict[str, Any]) -> None: ...


def _stop_intent_id(
    *,
    lifecycle_id: str,
    entity_id: str,
    service_domain: str,
    service_name: str,
    ends_at: str,
) -> str:
    payload = "\0".join(
        (lifecycle_id, entity_id, service_domain, service_name, ends_at)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _lease_id(*, lifecycle_id: str, stop_intent_id: str, plan_digest: str) -> str:
    payload = "\0".join((lifecycle_id, stop_intent_id, plan_digest))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def stop_lease_storage_key(entry_id: str) -> str:
    if not entry_id:
        raise StopLeaseError("entry_id is required")
    digest = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:20]
    return f"{DOMAIN}.load_execution_stop_leases.{digest}"


@dataclass(frozen=True, slots=True)
class ExecutionStopLease:
    """Immutable persisted obligation to stop one bounded future load run."""

    lease_id: str
    stop_intent_id: str
    entry_id: str
    lifecycle_id: str
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
    status: str
    created_at: int
    service_call_performed: bool = False
    executor_available: bool = False

    def validated(self) -> "ExecutionStopLease":
        if not _HEX_32.fullmatch(self.lease_id):
            raise StopLeaseError("lease_id must be a 32-character hex digest")
        if not _HEX_32.fullmatch(self.stop_intent_id):
            raise StopLeaseError("stop_intent_id must be a 32-character hex digest")
        if not self.entry_id or not self.lifecycle_id or not self.attempt_id:
            raise StopLeaseError("entry/lifecycle/attempt identity is required")
        if not self.action_snapshot_id or not self.profile_id or not self.entity_id:
            raise StopLeaseError("action/profile/entity identity is required")
        if not _HEX_64.fullmatch(self.approval_snapshot_digest):
            raise StopLeaseError("approval_snapshot_digest must be SHA-256 hex")
        if not _HEX_64.fullmatch(self.plan_digest):
            raise StopLeaseError("plan_digest must be SHA-256 hex")
        if self.status != STOP_LEASE_ARMED:
            raise StopLeaseError("new stop lease must remain armed/inert")
        if self.desired_state != "off":
            raise StopLeaseError("stop lease desired state must be off")
        if (self.service_domain, self.service_name) not in {
            ("switch", "turn_off"),
            ("input_boolean", "turn_off"),
        }:
            raise StopLeaseError("stop service mapping is not allowlisted")
        if self.created_at < 0:
            raise StopLeaseError("created_at must be non-negative")
        expected_intent = _stop_intent_id(
            lifecycle_id=self.lifecycle_id,
            entity_id=self.entity_id,
            service_domain=self.service_domain,
            service_name=self.service_name,
            ends_at=self.ends_at,
        )
        if self.stop_intent_id != expected_intent:
            raise StopLeaseError("stop intent identity does not match immutable binding")
        expected_lease = _lease_id(
            lifecycle_id=self.lifecycle_id,
            stop_intent_id=self.stop_intent_id,
            plan_digest=self.plan_digest,
        )
        if self.lease_id != expected_lease:
            raise StopLeaseError("stop lease identity does not match immutable binding")
        if self.service_call_performed or self.executor_available:
            raise StopLeaseError("stop lease cannot represent execution")
        return self

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExecutionStopLease":
        try:
            return cls(
                lease_id=str(value["lease_id"]),
                stop_intent_id=str(value["stop_intent_id"]),
                entry_id=str(value["entry_id"]),
                lifecycle_id=str(value["lifecycle_id"]),
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
                status=str(value["status"]),
                created_at=int(value["created_at"]),
                service_call_performed=bool(value.get("service_call_performed", False)),
                executor_available=bool(value.get("executor_available", False)),
            ).validated()
        except (KeyError, TypeError, ValueError) as err:
            if isinstance(err, StopLeaseError):
                raise
            raise StopLeaseError("invalid persisted stop lease") from err

    @classmethod
    def from_prepared_lifecycle(
        cls,
        lifecycle: ExecutionLifecycleRecord,
        *,
        created_at: int,
    ) -> "ExecutionStopLease":
        lifecycle.validated()
        if lifecycle.state != STATE_PREPARED:
            raise StopLeaseError("stop lease requires a prepared lifecycle")
        mapping = _STOP_SERVICE_BY_START.get(
            (lifecycle.service_domain, lifecycle.service_name)
        )
        if mapping is None:
            raise StopLeaseError("prepared start action has no safe stop mapping")
        stop_domain, stop_name = mapping
        intent_id = _stop_intent_id(
            lifecycle_id=lifecycle.lifecycle_id,
            entity_id=lifecycle.entity_id,
            service_domain=stop_domain,
            service_name=stop_name,
            ends_at=lifecycle.plan.ends_at,
        )
        return cls(
            lease_id=_lease_id(
                lifecycle_id=lifecycle.lifecycle_id,
                stop_intent_id=intent_id,
                plan_digest=lifecycle.plan_digest,
            ),
            stop_intent_id=intent_id,
            entry_id=lifecycle.entry_id,
            lifecycle_id=lifecycle.lifecycle_id,
            attempt_id=lifecycle.attempt_id,
            action_snapshot_id=lifecycle.action_snapshot_id,
            profile_id=lifecycle.profile_id,
            entity_id=lifecycle.entity_id,
            approval_snapshot_digest=lifecycle.approval_snapshot_digest,
            plan_digest=lifecycle.plan_digest,
            starts_at=lifecycle.plan.starts_at,
            ends_at=lifecycle.plan.ends_at,
            service_domain=stop_domain,
            service_name=stop_name,
            desired_state="off",
            status=STOP_LEASE_ARMED,
            created_at=created_at,
        ).validated()


@dataclass(frozen=True, slots=True)
class StopLeaseRecordResult:
    lease: ExecutionStopLease
    created: bool
    idempotent_replay: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "stop_lease": self.lease.as_dict(),
            "created": self.created,
            "idempotent_replay": self.idempotent_replay,
            "service_call_performed": False,
            "executor_available": False,
        }


class ExecutionStopLeaseLedger:
    def __init__(self, leases: tuple[ExecutionStopLease, ...] = ()) -> None:
        self._by_lifecycle: dict[str, ExecutionStopLease] = {}
        self._by_id: dict[str, ExecutionStopLease] = {}
        for lease in leases:
            item = lease.validated()
            if item.lifecycle_id in self._by_lifecycle:
                raise StopLeaseError(
                    f"duplicate stop lease for lifecycle: {item.lifecycle_id}"
                )
            if item.lease_id in self._by_id:
                raise StopLeaseError(f"duplicate stop lease id: {item.lease_id}")
            self._by_lifecycle[item.lifecycle_id] = item
            self._by_id[item.lease_id] = item

    @property
    def leases(self) -> tuple[ExecutionStopLease, ...]:
        return tuple(
            sorted(self._by_id.values(), key=lambda item: (item.created_at, item.lease_id))
        )

    def get_by_lifecycle_id(self, lifecycle_id: str) -> ExecutionStopLease | None:
        return self._by_lifecycle.get(lifecycle_id)

    def record(self, lease: ExecutionStopLease) -> StopLeaseRecordResult:
        candidate = lease.validated()
        existing = self._by_lifecycle.get(candidate.lifecycle_id)
        if existing is not None:
            if existing == candidate or existing.lease_id == candidate.lease_id:
                return StopLeaseRecordResult(existing, created=False, idempotent_replay=True)
            raise StopLeaseConflictError(
                "lifecycle already has a different immutable stop obligation"
            )
        if candidate.lease_id in self._by_id:
            raise StopLeaseConflictError("stop lease ID belongs to another lifecycle")
        self._by_lifecycle[candidate.lifecycle_id] = candidate
        self._by_id[candidate.lease_id] = candidate
        return StopLeaseRecordResult(candidate, created=True, idempotent_replay=False)

    def as_storage(self) -> dict[str, Any]:
        return {
            "schema_version": STOP_LEASE_SCHEMA_VERSION,
            "leases": [lease.as_dict() for lease in self.leases],
        }

    @classmethod
    def from_storage(cls, value: dict[str, Any] | None) -> "ExecutionStopLeaseLedger":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise StopLeaseError("stop lease storage must be an object")
        if value.get("schema_version") != STOP_LEASE_SCHEMA_VERSION:
            raise StopLeaseError("unsupported stop lease storage schema")
        raw = value.get("leases", [])
        if not isinstance(raw, list):
            raise StopLeaseError("stop leases must be a list")
        leases: list[ExecutionStopLease] = []
        for item in raw:
            if not isinstance(item, dict):
                raise StopLeaseError("stop lease record must be an object")
            leases.append(ExecutionStopLease.from_dict(item))
        return cls(tuple(leases))


class ExecutionStopLeaseRepository:
    def __init__(self, store: StopLeaseStore) -> None:
        self._store = store
        self._lock = asyncio.Lock()
        self._ledger: ExecutionStopLeaseLedger | None = None

    async def _async_ledger(self) -> ExecutionStopLeaseLedger:
        if self._ledger is None:
            self._ledger = ExecutionStopLeaseLedger.from_storage(
                await self._store.async_load()
            )
        return self._ledger

    async def async_record(self, lease: ExecutionStopLease) -> StopLeaseRecordResult:
        async with self._lock:
            current = await self._async_ledger()
            candidate = ExecutionStopLeaseLedger(current.leases)
            result = candidate.record(lease)
            if result.created:
                await self._store.async_save(candidate.as_storage())
                self._ledger = candidate
            return result

    async def async_get_by_lifecycle_id(
        self,
        lifecycle_id: str,
    ) -> ExecutionStopLease | None:
        async with self._lock:
            return (await self._async_ledger()).get_by_lifecycle_id(lifecycle_id)

    async def async_list(self) -> tuple[ExecutionStopLease, ...]:
        async with self._lock:
            return (await self._async_ledger()).leases


def home_assistant_stop_lease_repository(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionStopLeaseRepository:
    store = Store(
        hass,
        STOP_LEASE_STORAGE_VERSION,
        stop_lease_storage_key(entry_id),
    )
    return ExecutionStopLeaseRepository(store)
