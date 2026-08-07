"""Immutable execution lifecycle record for FRAKON Energy.

This module contains no persistence, WebSocket API, Home Assistant service call,
or executor. It only models one lifecycle record bound to an execution attempt
and its immutable action snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import re

from .load_execution_lifecycle_core import (
    STATUS_PREPARED,
    require_transition,
    validate_status,
)

_HEX_32 = re.compile(r"^[0-9a-f]{32}$")


def lifecycle_id_for(attempt_id: str, action_snapshot_id: str) -> str:
    """Return the deterministic lifecycle ID for one attempt/action binding."""
    if not attempt_id:
        raise ValueError("attempt_id is required")
    if not action_snapshot_id:
        raise ValueError("action_snapshot_id is required")
    payload = f"{attempt_id}\0{action_snapshot_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class ExecutionLifecycleRecord:
    """Immutable state of one future execution lifecycle."""

    lifecycle_id: str
    attempt_id: str
    action_snapshot_id: str
    status: str
    created_at: int
    updated_at: int
    revision: int = 0

    def validated(self) -> "ExecutionLifecycleRecord":
        if not _HEX_32.fullmatch(self.lifecycle_id):
            raise ValueError("lifecycle_id must be a 32-character hex digest")
        expected = lifecycle_id_for(self.attempt_id, self.action_snapshot_id)
        if self.lifecycle_id != expected:
            raise ValueError("lifecycle_id does not match attempt/action binding")
        validate_status(self.status)
        if self.created_at < 0 or self.updated_at < self.created_at:
            raise ValueError("lifecycle timestamps are invalid")
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
        if self.status == STATUS_PREPARED and self.revision != 0:
            raise ValueError("prepared lifecycle must have revision 0")
        return self

    @classmethod
    def prepared(
        cls,
        *,
        attempt_id: str,
        action_snapshot_id: str,
        created_at: int,
    ) -> "ExecutionLifecycleRecord":
        """Create the initial immutable prepared record."""
        return cls(
            lifecycle_id=lifecycle_id_for(attempt_id, action_snapshot_id),
            attempt_id=attempt_id,
            action_snapshot_id=action_snapshot_id,
            status=STATUS_PREPARED,
            created_at=created_at,
            updated_at=created_at,
            revision=0,
        ).validated()

    def transition_to(self, target_status: str, *, updated_at: int) -> "ExecutionLifecycleRecord":
        """Return a new record after one allowlisted lifecycle transition."""
        self.validated()
        require_transition(self.status, target_status)
        if updated_at < self.updated_at:
            raise ValueError("updated_at cannot move backwards")
        return replace(
            self,
            status=target_status,
            updated_at=updated_at,
            revision=self.revision + 1,
        ).validated()
