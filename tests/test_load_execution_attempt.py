from dataclasses import replace
from typing import Any

import pytest

from custom_components.frakon_energy.load_execution_approval import ExecutionApproval
from custom_components.frakon_energy.load_execution_attempt import (
    ATTEMPT_STATUS_APPROVAL_CONSUMED,
    AttemptConflictError,
    ExecutionAttempt,
    ExecutionAttemptLedger,
    ExecutionAttemptRepository,
    approval_artifact_fingerprint,
    attempt_storage_key,
)


class _FakeStore:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = data
        self.saves = 0
        self.fail_save = False

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        if self.fail_save:
            raise RuntimeError("storage unavailable")
        self.saves += 1
        self.data = data


def _approval(*, approval_id: str = "approval-1", signature: str = "signature-a") -> ExecutionApproval:
    return ExecutionApproval(
        approval_id=approval_id,
        intent="execute_load_plan",
        snapshot_digest="a" * 64,
        issued_at=100,
        expires_at=220,
        signature=signature,
    )


def _attempt(
    *,
    approval: ExecutionApproval | None = None,
    entry_id: str = "entry-1",
    created_at: int = 110,
) -> ExecutionAttempt:
    return ExecutionAttempt.from_consumed_approval(
        entry_id=entry_id,
        profile_id="ev-home",
        entity_id="switch.enyaq_charging",
        approval=approval or _approval(),
        created_at=created_at,
    )


def test_approval_fingerprint_covers_complete_artifact() -> None:
    approval = _approval()
    same = _approval()
    changed = replace(approval, signature="signature-b")

    assert approval_artifact_fingerprint(approval) == approval_artifact_fingerprint(same)
    assert approval_artifact_fingerprint(approval) != approval_artifact_fingerprint(changed)


def test_attempt_identity_is_deterministic_per_entry_and_approval() -> None:
    first = _attempt(entry_id="entry-1")
    retry = _attempt(entry_id="entry-1", created_at=150)
    other_entry = _attempt(entry_id="entry-2")

    assert first.attempt_id == retry.attempt_id
    assert first.attempt_id != other_entry.attempt_id
    assert first.status == ATTEMPT_STATUS_APPROVAL_CONSUMED
    assert first.execution_performed is False
    assert first.executor_available is False


def test_ledger_returns_existing_attempt_for_identical_retry() -> None:
    ledger = ExecutionAttemptLedger()
    first = ledger.record(_attempt(created_at=110))
    retry = ledger.record(_attempt(created_at=160))

    assert first.created is True
    assert first.idempotent_replay is False
    assert retry.created is False
    assert retry.idempotent_replay is True
    assert retry.attempt == first.attempt
    assert len(ledger.attempts) == 1


def test_same_approval_id_with_changed_artifact_is_conflict() -> None:
    ledger = ExecutionAttemptLedger()
    ledger.record(_attempt())
    changed = _attempt(approval=_approval(signature="signature-b"))

    with pytest.raises(AttemptConflictError, match="different artifact or scope"):
        ledger.record(changed)


def test_storage_round_trip_preserves_attempts() -> None:
    ledger = ExecutionAttemptLedger()
    ledger.record(_attempt())
    ledger.record(_attempt(approval=_approval(approval_id="approval-2"), created_at=120))

    restored = ExecutionAttemptLedger.from_storage(ledger.as_storage())

    assert restored.attempts == ledger.attempts
    assert restored.get_by_approval_id("approval-1") == ledger.get_by_approval_id("approval-1")


def test_storage_rejects_record_claiming_execution_happened() -> None:
    raw = _attempt().as_dict()
    raw["execution_performed"] = True

    with pytest.raises(ValueError, match="cannot represent performed execution"):
        ExecutionAttemptLedger.from_storage({"schema_version": 1, "attempts": [raw]})


@pytest.mark.asyncio
async def test_repository_persists_once_and_retry_is_idempotent() -> None:
    store = _FakeStore()
    repository = ExecutionAttemptRepository(store)

    first = await repository.async_record(_attempt())
    retry = await repository.async_record(_attempt(created_at=180))

    assert first.created is True
    assert retry.idempotent_replay is True
    assert store.saves == 1
    assert len(await repository.async_list()) == 1


@pytest.mark.asyncio
async def test_repository_does_not_commit_in_memory_when_save_fails() -> None:
    store = _FakeStore()
    repository = ExecutionAttemptRepository(store)
    store.fail_save = True

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await repository.async_record(_attempt())

    assert await repository.async_list() == ()
    assert store.saves == 0


@pytest.mark.asyncio
async def test_repository_loads_existing_persisted_attempts() -> None:
    ledger = ExecutionAttemptLedger()
    ledger.record(_attempt())
    store = _FakeStore(ledger.as_storage())
    repository = ExecutionAttemptRepository(store)

    restored = await repository.async_get_by_approval_id("approval-1")

    assert restored == _attempt()
    assert store.saves == 0


def test_storage_key_is_stable_and_isolated_per_config_entry() -> None:
    first = attempt_storage_key("entry-1")
    again = attempt_storage_key("entry-1")
    other = attempt_storage_key("entry-2")

    assert first == again
    assert first != other
    assert first.startswith("frakon_energy.load_execution_attempts.")


def test_attempt_from_dict_rejects_available_executor_claim() -> None:
    raw = _attempt().as_dict()
    raw["executor_available"] = True

    with pytest.raises(ValueError, match="cannot represent an available executor"):
        ExecutionAttempt.from_dict(raw)
