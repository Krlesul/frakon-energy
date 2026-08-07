from dataclasses import FrozenInstanceError, replace

import pytest

from custom_components.frakon_energy.load_execution_lifecycle_core import (
    STATUS_CANCELLED,
    STATUS_DISPATCHING,
    STATUS_PREPARED,
    STATUS_RECOVERY_REQUIRED,
    STATUS_VERIFYING,
    InvalidLifecycleTransition,
)
from custom_components.frakon_energy.load_execution_lifecycle_record import (
    ExecutionLifecycleRecord,
    lifecycle_id_for,
)


def _prepared(*, created_at: int = 100) -> ExecutionLifecycleRecord:
    return ExecutionLifecycleRecord.prepared(
        attempt_id="attempt-1",
        action_snapshot_id="a" * 32,
        created_at=created_at,
    )


def test_lifecycle_id_is_deterministic_and_binding_sensitive() -> None:
    first = lifecycle_id_for("attempt-1", "a" * 32)
    again = lifecycle_id_for("attempt-1", "a" * 32)
    other_attempt = lifecycle_id_for("attempt-2", "a" * 32)
    other_snapshot = lifecycle_id_for("attempt-1", "b" * 32)

    assert first == again
    assert len(first) == 32
    assert first != other_attempt
    assert first != other_snapshot


def test_prepared_record_has_initial_revision_and_timestamps() -> None:
    record = _prepared(created_at=123)

    assert record.lifecycle_id == lifecycle_id_for("attempt-1", "a" * 32)
    assert record.attempt_id == "attempt-1"
    assert record.action_snapshot_id == "a" * 32
    assert record.status == STATUS_PREPARED
    assert record.created_at == 123
    assert record.updated_at == 123
    assert record.revision == 0


def test_record_is_frozen() -> None:
    record = _prepared()

    with pytest.raises(FrozenInstanceError):
        record.status = STATUS_DISPATCHING  # type: ignore[misc]


def test_transition_returns_new_record_and_keeps_binding() -> None:
    prepared = _prepared()
    dispatching = prepared.transition_to(STATUS_DISPATCHING, updated_at=101)

    assert prepared.status == STATUS_PREPARED
    assert prepared.revision == 0
    assert dispatching is not prepared
    assert dispatching.lifecycle_id == prepared.lifecycle_id
    assert dispatching.attempt_id == prepared.attempt_id
    assert dispatching.action_snapshot_id == prepared.action_snapshot_id
    assert dispatching.status == STATUS_DISPATCHING
    assert dispatching.created_at == prepared.created_at
    assert dispatching.updated_at == 101
    assert dispatching.revision == 1


def test_multiple_allowlisted_transitions_increment_revision() -> None:
    record = _prepared()
    record = record.transition_to(STATUS_DISPATCHING, updated_at=101)
    record = record.transition_to(STATUS_VERIFYING, updated_at=102)
    record = record.transition_to(STATUS_RECOVERY_REQUIRED, updated_at=103)

    assert record.status == STATUS_RECOVERY_REQUIRED
    assert record.revision == 3
    assert record.updated_at == 103


def test_cancel_from_prepared_is_allowed() -> None:
    cancelled = _prepared().transition_to(STATUS_CANCELLED, updated_at=101)

    assert cancelled.status == STATUS_CANCELLED
    assert cancelled.revision == 1


def test_forbidden_transition_fails_closed() -> None:
    with pytest.raises(InvalidLifecycleTransition):
        _prepared().transition_to(STATUS_VERIFYING, updated_at=101)


def test_transition_time_cannot_move_backwards() -> None:
    dispatching = _prepared(created_at=100).transition_to(
        STATUS_DISPATCHING,
        updated_at=110,
    )

    with pytest.raises(ValueError, match="cannot move backwards"):
        dispatching.transition_to(STATUS_VERIFYING, updated_at=109)


def test_validation_rejects_tampered_lifecycle_id() -> None:
    record = replace(_prepared(), lifecycle_id="0" * 32)

    with pytest.raises(ValueError, match="does not match"):
        record.validated()


def test_prepared_record_cannot_claim_nonzero_revision() -> None:
    record = replace(_prepared(), revision=1)

    with pytest.raises(ValueError, match="revision 0"):
        record.validated()


def test_invalid_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="timestamps"):
        ExecutionLifecycleRecord.prepared(
            attempt_id="attempt-1",
            action_snapshot_id="a" * 32,
            created_at=-1,
        )

    with pytest.raises(ValueError, match="timestamps"):
        replace(_prepared(), updated_at=99).validated()


def test_missing_identity_parts_are_rejected() -> None:
    with pytest.raises(ValueError, match="attempt_id"):
        lifecycle_id_for("", "a" * 32)

    with pytest.raises(ValueError, match="action_snapshot_id"):
        lifecycle_id_for("attempt-1", "")
