"""Persistent immutable action snapshots bound to execution attempts.

This module still performs no Home Assistant service call. It binds a consumed
execution attempt to one strict allowlisted action intent, persists that binding,
and can revalidate the current profile/entity state before any later executor is
introduced.
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
from .load_action_intent import (
    ACTION_STATE_BLOCKED,
    ActionStateDecision,
    LoadActionIntent,
    UnsupportedActionIntentError,
    evaluate_action_current_state,
    resolve_start_action_intent,
)
from .load_execution_attempt import ExecutionAttempt
from .load_profiles import LoadProfile

ACTION_SNAPSHOT_STORAGE_VERSION = 1
ACTION_SNAPSHOT_SCHEMA_VERSION = 1
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class ActionSnapshotConflictError(ValueError):
    """Raised when one attempt is rebound to a different immutable action."""


class ActionSnapshotStore(Protocol):
    async def async_load(self) -> dict[str, Any] | None: ...
    async def async_save(self, data: dict[str, Any]) -> None: ...


def action_snapshot_storage_key(entry_id: str) -> str:
    if not entry_id:
        raise ValueError("entry_id is required")
    digest = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:20]
    return f"{DOMAIN}.load_execution_action_snapshots.{digest}"


def _snapshot_id_from_fields(
    attempt_id: str,
    approval_fingerprint: str,
    approval_snapshot_digest: str,
    action_intent_id: str,
) -> str:
    payload = "\0".join(
        (attempt_id, approval_fingerprint, approval_snapshot_digest, action_intent_id)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _snapshot_identity(attempt: ExecutionAttempt, intent: LoadActionIntent) -> str:
    return _snapshot_id_from_fields(
        attempt.attempt_id,
        attempt.approval_fingerprint,
        attempt.snapshot_digest,
        intent.intent_id,
    )


@dataclass(frozen=True, slots=True)
class ExecutionActionSnapshot:
    snapshot_id: str
    attempt_id: str
    entry_id: str
    profile_id: str
    approval_id: str
    approval_fingerprint: str
    approval_snapshot_digest: str
    action_intent_id: str
    action: str
    profile_kind: str
    entity_id: str
    entity_domain: str
    service_domain: str
    service_name: str
    desired_state: str
    created_at: int
    service_call_performed: bool = False
    executor_available: bool = False

    def validated(self) -> "ExecutionActionSnapshot":
        if not _HEX_32.fullmatch(self.snapshot_id):
            raise ValueError("snapshot_id must be a deterministic 32-character hex digest")
        if not self.attempt_id:
            raise ValueError("attempt_id is required")
        if not self.entry_id or not self.profile_id or not self.approval_id:
            raise ValueError("entry/profile/approval identity is required")
        if not _HEX_64.fullmatch(self.approval_fingerprint):
            raise ValueError("approval_fingerprint must be a SHA-256 hex digest")
        if not _HEX_64.fullmatch(self.approval_snapshot_digest):
            raise ValueError("approval_snapshot_digest must be a SHA-256 hex digest")
        if not _HEX_32.fullmatch(self.action_intent_id):
            raise ValueError("action_intent_id must be a deterministic 32-character hex digest")
        expected_snapshot_id = _snapshot_id_from_fields(
            self.attempt_id,
            self.approval_fingerprint,
            self.approval_snapshot_digest,
            self.action_intent_id,
        )
        if self.snapshot_id != expected_snapshot_id:
            raise ValueError("action snapshot identity does not match its immutable binding")
        if self.created_at < 0:
            raise ValueError("created_at must be non-negative")
        LoadActionIntent(
            intent_id=self.action_intent_id,
            action=self.action,
            profile_id=self.profile_id,
            profile_kind=self.profile_kind,
            entity_id=self.entity_id,
            entity_domain=self.entity_domain,
            service_domain=self.service_domain,
            service_name=self.service_name,
            target={"entity_id": self.entity_id},
            service_data={},
            desired_state=self.desired_state,
        ).validated()
        if self.service_call_performed:
            raise ValueError("action snapshot cannot represent a performed service call")
        if self.executor_available:
            raise ValueError("action snapshot cannot represent an available executor")
        return self

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExecutionActionSnapshot":
        return cls(
            snapshot_id=str(value.get("snapshot_id", "")),
            attempt_id=str(value.get("attempt_id", "")),
            entry_id=str(value.get("entry_id", "")),
            profile_id=str(value.get("profile_id", "")),
            approval_id=str(value.get("approval_id", "")),
            approval_fingerprint=str(value.get("approval_fingerprint", "")),
            approval_snapshot_digest=str(value.get("approval_snapshot_digest", "")),
            action_intent_id=str(value.get("action_intent_id", "")),
            action=str(value.get("action", "")),
            profile_kind=str(value.get("profile_kind", "")),
            entity_id=str(value.get("entity_id", "")),
            entity_domain=str(value.get("entity_domain", "")),
            service_domain=str(value.get("service_domain", "")),
            service_name=str(value.get("service_name", "")),
            desired_state=str(value.get("desired_state", "")),
            created_at=int(value.get("created_at", -1)),
            service_call_performed=bool(value.get("service_call_performed", False)),
            executor_available=bool(value.get("executor_available", False)),
        ).validated()

    @classmethod
    def from_attempt_and_intent(
        cls,
        *,
        attempt: ExecutionAttempt,
        intent: LoadActionIntent,
        created_at: int,
    ) -> "ExecutionActionSnapshot":
        attempt.validated()
        intent.validated()
        if attempt.profile_id != intent.profile_id:
            raise ValueError("attempt and action intent profile mismatch")
        if attempt.entity_id is None or attempt.entity_id != intent.entity_id:
            raise ValueError("attempt and action intent entity mismatch")
        if created_at < attempt.created_at:
            raise ValueError("action snapshot cannot predate the execution attempt")
        return cls(
            snapshot_id=_snapshot_identity(attempt, intent),
            attempt_id=attempt.attempt_id,
            entry_id=attempt.entry_id,
            profile_id=attempt.profile_id,
            approval_id=attempt.approval_id,
            approval_fingerprint=attempt.approval_fingerprint,
            approval_snapshot_digest=attempt.snapshot_digest,
            action_intent_id=intent.intent_id,
            action=intent.action,
            profile_kind=intent.profile_kind,
            entity_id=intent.entity_id,
            entity_domain=intent.entity_domain,
            service_domain=intent.service_domain,
            service_name=intent.service_name,
            desired_state=intent.desired_state,
            created_at=created_at,
        ).validated()


@dataclass(frozen=True, slots=True)
class ActionSnapshotRecordResult:
    snapshot: ExecutionActionSnapshot
    created: bool
    idempotent_replay: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot": self.snapshot.as_dict(),
            "created": self.created,
            "idempotent_replay": self.idempotent_replay,
            "service_call_performed": False,
            "executor_available": False,
        }


@dataclass(frozen=True, slots=True)
class ActionSnapshotRevalidation:
    status: str
    reason: str
    current_state: str | None
    desired_state: str
    attempt_matches: bool
    profile_matches: bool
    service_call_performed: bool = False
    executor_available: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _attempt_matches(snapshot: ExecutionActionSnapshot, attempt: ExecutionAttempt) -> bool:
    attempt.validated()
    return (
        snapshot.attempt_id == attempt.attempt_id
        and snapshot.entry_id == attempt.entry_id
        and snapshot.profile_id == attempt.profile_id
        and snapshot.entity_id == attempt.entity_id
        and snapshot.approval_id == attempt.approval_id
        and snapshot.approval_fingerprint == attempt.approval_fingerprint
        and snapshot.approval_snapshot_digest == attempt.snapshot_digest
    )


def _profile_matches(snapshot: ExecutionActionSnapshot, profile: LoadProfile) -> bool:
    try:
        profile.validated()
        current = resolve_start_action_intent(profile)
    except (ValueError, UnsupportedActionIntentError):
        return False
    return (
        profile.enabled
        and current.intent_id == snapshot.action_intent_id
        and current.profile_id == snapshot.profile_id
        and current.profile_kind == snapshot.profile_kind
        and current.entity_id == snapshot.entity_id
        and current.entity_domain == snapshot.entity_domain
        and current.service_domain == snapshot.service_domain
        and current.service_name == snapshot.service_name
        and current.desired_state == snapshot.desired_state
    )


def revalidate_action_snapshot(
    snapshot: ExecutionActionSnapshot,
    *,
    attempt: ExecutionAttempt,
    profile: LoadProfile,
    current_state: str | None,
) -> ActionSnapshotRevalidation:
    snapshot.validated()
    attempt_matches = _attempt_matches(snapshot, attempt)
    if not attempt_matches:
        return ActionSnapshotRevalidation(
            status=ACTION_STATE_BLOCKED,
            reason="execution_attempt_changed",
            current_state=current_state,
            desired_state=snapshot.desired_state,
            attempt_matches=False,
            profile_matches=False,
        )
    profile_matches = _profile_matches(snapshot, profile)
    if not profile_matches:
        return ActionSnapshotRevalidation(
            status=ACTION_STATE_BLOCKED,
            reason="profile_or_action_mapping_changed",
            current_state=current_state,
            desired_state=snapshot.desired_state,
            attempt_matches=True,
            profile_matches=False,
        )
    intent = resolve_start_action_intent(profile)
    decision: ActionStateDecision = evaluate_action_current_state(intent, current_state)
    return ActionSnapshotRevalidation(
        status=decision.status,
        reason=decision.reason,
        current_state=decision.current_state,
        desired_state=decision.desired_state,
        attempt_matches=True,
        profile_matches=True,
    )


class ExecutionActionSnapshotLedger:
    def __init__(self, snapshots: tuple[ExecutionActionSnapshot, ...] = ()) -> None:
        self._by_attempt: dict[str, ExecutionActionSnapshot] = {}
        self._by_snapshot: dict[str, ExecutionActionSnapshot] = {}
        for snapshot in snapshots:
            validated = snapshot.validated()
            if validated.attempt_id in self._by_attempt:
                raise ValueError(f"duplicate action snapshot for attempt: {validated.attempt_id}")
            if validated.snapshot_id in self._by_snapshot:
                raise ValueError(f"duplicate action snapshot id: {validated.snapshot_id}")
            self._by_attempt[validated.attempt_id] = validated
            self._by_snapshot[validated.snapshot_id] = validated

    @property
    def snapshots(self) -> tuple[ExecutionActionSnapshot, ...]:
        return tuple(sorted(self._by_snapshot.values(), key=lambda item: (item.created_at, item.snapshot_id)))

    def get_by_attempt_id(self, attempt_id: str) -> ExecutionActionSnapshot | None:
        return self._by_attempt.get(attempt_id)

    def record(self, snapshot: ExecutionActionSnapshot) -> ActionSnapshotRecordResult:
        candidate = snapshot.validated()
        existing = self._by_attempt.get(candidate.attempt_id)
        if existing is not None:
            if existing == candidate:
                return ActionSnapshotRecordResult(existing, created=False, idempotent_replay=True)
            raise ActionSnapshotConflictError(
                "execution attempt already has a different immutable action snapshot"
            )
        if candidate.snapshot_id in self._by_snapshot:
            raise ActionSnapshotConflictError("snapshot_id already belongs to another attempt")
        self._by_attempt[candidate.attempt_id] = candidate
        self._by_snapshot[candidate.snapshot_id] = candidate
        return ActionSnapshotRecordResult(candidate, created=True, idempotent_replay=False)

    def as_storage(self) -> dict[str, Any]:
        return {
            "schema_version": ACTION_SNAPSHOT_SCHEMA_VERSION,
            "snapshots": [snapshot.as_dict() for snapshot in self.snapshots],
        }

    @classmethod
    def from_storage(cls, value: dict[str, Any] | None) -> "ExecutionActionSnapshotLedger":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("action snapshot storage must be an object")
        if value.get("schema_version") != ACTION_SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("unsupported action snapshot storage schema")
        raw_snapshots = value.get("snapshots", [])
        if not isinstance(raw_snapshots, list):
            raise ValueError("snapshots must be a list")
        snapshots: list[ExecutionActionSnapshot] = []
        for raw in raw_snapshots:
            if not isinstance(raw, dict):
                raise ValueError("action snapshot record must be an object")
            snapshots.append(ExecutionActionSnapshot.from_dict(raw))
        return cls(tuple(snapshots))


class ExecutionActionSnapshotRepository:
    def __init__(self, store: ActionSnapshotStore) -> None:
        self._store = store
        self._lock = asyncio.Lock()
        self._ledger: ExecutionActionSnapshotLedger | None = None

    async def _async_ledger(self) -> ExecutionActionSnapshotLedger:
        if self._ledger is None:
            self._ledger = ExecutionActionSnapshotLedger.from_storage(await self._store.async_load())
        return self._ledger

    async def async_record(
        self,
        snapshot: ExecutionActionSnapshot,
    ) -> ActionSnapshotRecordResult:
        async with self._lock:
            current = await self._async_ledger()
            candidate = ExecutionActionSnapshotLedger(current.snapshots)
            result = candidate.record(snapshot)
            if result.created:
                await self._store.async_save(candidate.as_storage())
                self._ledger = candidate
            return result

    async def async_list(self) -> tuple[ExecutionActionSnapshot, ...]:
        async with self._lock:
            return (await self._async_ledger()).snapshots

    async def async_get_by_attempt_id(
        self,
        attempt_id: str,
    ) -> ExecutionActionSnapshot | None:
        async with self._lock:
            return (await self._async_ledger()).get_by_attempt_id(attempt_id)


def home_assistant_action_snapshot_repository(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionActionSnapshotRepository:
    store = Store(
        hass,
        ACTION_SNAPSHOT_STORAGE_VERSION,
        action_snapshot_storage_key(entry_id),
    )
    return ExecutionActionSnapshotRepository(store)
