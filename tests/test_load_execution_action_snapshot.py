from dataclasses import replace
from typing import Any

import pytest

from custom_components.frakon_energy.load_action_intent import (
    ACTION_STATE_ALREADY_SATISFIED,
    ACTION_STATE_BLOCKED,
    ACTION_STATE_READY,
    resolve_start_action_intent,
)
from custom_components.frakon_energy.load_execution_action_snapshot import (
    ActionSnapshotConflictError,
    ExecutionActionSnapshot,
    ExecutionActionSnapshotLedger,
    ExecutionActionSnapshotRepository,
    action_snapshot_storage_key,
    revalidate_action_snapshot,
)
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_profiles import (
    PROFILE_KIND_EV,
    LoadProfile,
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


def _attempt(
    *,
    entity_id: str | None = "switch.enyaq_charging",
    attempt_id: str = "attempt-1",
    profile_id: str = "ev-home",
) -> ExecutionAttempt:
    return ExecutionAttempt(
        attempt_id=attempt_id,
        entry_id="entry-1",
        profile_id=profile_id,
        entity_id=entity_id,
        approval_id="approval-1",
        approval_fingerprint="a" * 64,
        snapshot_digest="b" * 64,
        intent="execute_load_plan",
        approval_issued_at=100,
        approval_expires_at=220,
        created_at=110,
    ).validated()


def _profile(
    *,
    entity_id: str | None = "switch.enyaq_charging",
    enabled: bool = True,
    profile_id: str = "ev-home",
) -> LoadProfile:
    return LoadProfile(
        profile_id,
        "Enyaq charging",
        PROFILE_KIND_EV,
        60,
        11.0,
        enabled=enabled,
        entity_id=entity_id,
    )


def _snapshot(
    *,
    attempt: ExecutionAttempt | None = None,
    profile: LoadProfile | None = None,
    created_at: int = 111,
) -> ExecutionActionSnapshot:
    current_attempt = attempt or _attempt()
    current_profile = profile or _profile()
    intent = resolve_start_action_intent(current_profile)
    return ExecutionActionSnapshot.from_attempt_and_intent(
        attempt=current_attempt,
        intent=intent,
        created_at=created_at,
    )


def test_snapshot_binds_exact_attempt_approval_and_action() -> None:
    snapshot = _snapshot()

    assert snapshot.attempt_id == "attempt-1"
    assert snapshot.profile_id == "ev-home"
    assert snapshot.entity_id == "switch.enyaq_charging"
    assert snapshot.approval_id == "approval-1"
    assert snapshot.approval_fingerprint == "a" * 64
    assert snapshot.approval_snapshot_digest == "b" * 64
    assert snapshot.service_domain == "switch"
    assert snapshot.service_name == "turn_on"
    assert snapshot.desired_state == "on"
    assert snapshot.service_call_performed is False
    assert snapshot.executor_available is False


def test_snapshot_identity_is_deterministic_for_same_attempt_and_intent() -> None:
    first = _snapshot(created_at=111)
    second = _snapshot(created_at=120)

    assert first.snapshot_id == second.snapshot_id
    assert first.action_intent_id == second.action_intent_id


def test_snapshot_rejects_attempt_entity_mismatch() -> None:
    attempt = _attempt(entity_id="switch.other")
    intent = resolve_start_action_intent(_profile())

    with pytest.raises(ValueError, match="entity mismatch"):
        ExecutionActionSnapshot.from_attempt_and_intent(
            attempt=attempt,
            intent=intent,
            created_at=111,
        )


def test_snapshot_cannot_predate_attempt() -> None:
    with pytest.raises(ValueError, match="cannot predate"):
        _snapshot(created_at=109)


def test_storage_rejects_execution_claims() -> None:
    raw = _snapshot().as_dict()
    raw["service_call_performed"] = True

    with pytest.raises(ValueError, match="performed service call"):
        ExecutionActionSnapshot.from_dict(raw)


def test_storage_rejects_tampered_snapshot_identity() -> None:
    raw = _snapshot().as_dict()
    raw["snapshot_id"] = "0" * 32

    with pytest.raises(ValueError, match="identity does not match"):
        ExecutionActionSnapshot.from_dict(raw)


def test_storage_rejects_non_hex_approval_digest() -> None:
    raw = _snapshot().as_dict()
    raw["approval_fingerprint"] = "z" * 64

    with pytest.raises(ValueError, match="SHA-256 hex digest"):
        ExecutionActionSnapshot.from_dict(raw)


def test_ledger_is_idempotent_for_exact_snapshot() -> None:
    ledger = ExecutionActionSnapshotLedger()
    snapshot = _snapshot()

    first = ledger.record(snapshot)
    retry = ledger.record(snapshot)

    assert first.created is True
    assert first.idempotent_replay is False
    assert retry.created is False
    assert retry.idempotent_replay is True
    assert retry.snapshot == snapshot
    assert len(ledger.snapshots) == 1


def test_ledger_rejects_rebinding_attempt_to_different_action() -> None:
    ledger = ExecutionActionSnapshotLedger()
    first = _snapshot()
    ledger.record(first)

    changed_profile = _profile(entity_id="switch.second_charger")
    changed_attempt = _attempt(entity_id="switch.second_charger")
    changed = _snapshot(attempt=changed_attempt, profile=changed_profile)

    assert changed.attempt_id == first.attempt_id
    assert changed.snapshot_id != first.snapshot_id
    with pytest.raises(ActionSnapshotConflictError, match="different immutable action"):
        ledger.record(changed)


def test_storage_round_trip_preserves_snapshot() -> None:
    ledger = ExecutionActionSnapshotLedger()
    snapshot = _snapshot()
    ledger.record(snapshot)

    restored = ExecutionActionSnapshotLedger.from_storage(ledger.as_storage())

    assert restored.snapshots == (snapshot,)
    assert restored.get_by_attempt_id(snapshot.attempt_id) == snapshot


def test_revalidation_off_is_ready() -> None:
    snapshot = _snapshot()
    result = revalidate_action_snapshot(
        snapshot,
        attempt=_attempt(),
        profile=_profile(),
        current_state="off",
    )

    assert result.status == ACTION_STATE_READY
    assert result.reason == "entity_state_allows_start"
    assert result.attempt_matches is True
    assert result.profile_matches is True
    assert result.service_call_performed is False
    assert result.executor_available is False


def test_revalidation_on_is_already_satisfied() -> None:
    result = revalidate_action_snapshot(
        _snapshot(),
        attempt=_attempt(),
        profile=_profile(),
        current_state="on",
    )

    assert result.status == ACTION_STATE_ALREADY_SATISFIED
    assert result.reason == "entity_already_in_desired_state"


@pytest.mark.parametrize("state", [None, "unknown", "unavailable", "idle"])
def test_revalidation_non_allowlisted_state_is_blocked(state: str | None) -> None:
    result = revalidate_action_snapshot(
        _snapshot(),
        attempt=_attempt(),
        profile=_profile(),
        current_state=state,
    )

    assert result.status == ACTION_STATE_BLOCKED
    assert result.service_call_performed is False
    assert result.executor_available is False


def test_revalidation_blocks_changed_attempt_scope() -> None:
    changed_attempt = replace(_attempt(), snapshot_digest="c" * 64)

    result = revalidate_action_snapshot(
        _snapshot(),
        attempt=changed_attempt,
        profile=_profile(),
        current_state="off",
    )

    assert result.status == ACTION_STATE_BLOCKED
    assert result.reason == "execution_attempt_changed"
    assert result.attempt_matches is False
    assert result.profile_matches is False


def test_revalidation_blocks_changed_profile_binding() -> None:
    result = revalidate_action_snapshot(
        _snapshot(),
        attempt=_attempt(),
        profile=_profile(entity_id="switch.other"),
        current_state="off",
    )

    assert result.status == ACTION_STATE_BLOCKED
    assert result.reason == "profile_or_action_mapping_changed"
    assert result.attempt_matches is True
    assert result.profile_matches is False


def test_revalidation_blocks_disabled_profile() -> None:
    result = revalidate_action_snapshot(
        _snapshot(),
        attempt=_attempt(),
        profile=_profile(enabled=False),
        current_state="off",
    )

    assert result.status == ACTION_STATE_BLOCKED
    assert result.reason == "profile_or_action_mapping_changed"


@pytest.mark.asyncio
async def test_repository_persists_once_and_retry_is_idempotent() -> None:
    store = _FakeStore()
    repository = ExecutionActionSnapshotRepository(store)
    snapshot = _snapshot()

    first = await repository.async_record(snapshot)
    retry = await repository.async_record(snapshot)

    assert first.created is True
    assert retry.idempotent_replay is True
    assert store.saves == 1
    assert await repository.async_get_by_attempt_id(snapshot.attempt_id) == snapshot


@pytest.mark.asyncio
async def test_repository_rolls_back_in_memory_when_save_fails() -> None:
    store = _FakeStore()
    store.fail_save = True
    repository = ExecutionActionSnapshotRepository(store)

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await repository.async_record(_snapshot())

    assert await repository.async_list() == ()
    assert store.saves == 0


@pytest.mark.asyncio
async def test_repository_loads_existing_storage_without_rewrite() -> None:
    ledger = ExecutionActionSnapshotLedger()
    snapshot = _snapshot()
    ledger.record(snapshot)
    store = _FakeStore(ledger.as_storage())
    repository = ExecutionActionSnapshotRepository(store)

    restored = await repository.async_get_by_attempt_id(snapshot.attempt_id)

    assert restored == snapshot
    assert store.saves == 0


def test_storage_key_is_stable_and_isolated_per_entry() -> None:
    first = action_snapshot_storage_key("entry-1")
    again = action_snapshot_storage_key("entry-1")
    other = action_snapshot_storage_key("entry-2")

    assert first == again
    assert first != other
    assert first.startswith("frakon_energy.load_execution_action_snapshots.")
