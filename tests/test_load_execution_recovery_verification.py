from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_recovery_verification as verification
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import (
    CALL_CONFIRMED,
    CALL_UNKNOWN,
    STATE_DISPATCHED,
    STATE_RECOVERY_REQUIRED,
    STATE_VERIFIED,
    VERIFY_CONFIRMED,
    ExecutionLifecycleRecord,
    ExecutionLifecycleRepository,
    begin_dispatch,
    confirm_dispatch,
    require_recovery_after_restart,
)
from custom_components.frakon_energy.load_execution_lifecycle_recovery import (
    LifecycleRecoveryBlockedError,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import evaluate_execution_readiness
from custom_components.frakon_energy.load_execution_start_stop_ownership import StartStopOwnershipProof
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)


class _FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.saves = 0
        self.fail_save = False

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        if self.fail_save:
            raise RuntimeError("storage unavailable")
        self.data = data
        self.saves += 1


class _FakeStates:
    def __init__(self, state: str | None) -> None:
        self.state = state

    def get(self, entity_id: str) -> object | None:
        return SimpleNamespace(state=self.state) if self.state is not None else None


class _FakeHass:
    def __init__(self, state: str | None) -> None:
        self.data: dict[str, Any] = {}
        self.states = _FakeStates(state)


def _profile() -> LoadProfile:
    return LoadProfile(
        "ev-home",
        "Enyaq",
        PROFILE_KIND_EV,
        120,
        11.0,
        entity_id="switch.enyaq_charging",
    )


def _policy() -> LoadExecutionPolicy:
    return LoadExecutionPolicy(
        "ev-home",
        mode=EXECUTION_MODE_APPROVAL_REQUIRED,
        max_power_kw=11.0,
        max_duration_minutes=120,
    )


def _plan() -> LoadPlan:
    duration = 120
    power = 11.0
    average = 2.0
    energy = power * duration / 60
    return LoadPlan(
        load_id="ev-home",
        name="Enyaq",
        starts_at=START.isoformat(),
        ends_at=(START + timedelta(minutes=duration)).isoformat(),
        duration_minutes=duration,
        interval_count=8,
        power_kw=power,
        average_czk_kwh=average,
        minimum_czk_kwh=1.5,
        maximum_czk_kwh=2.5,
        estimated_energy_kwh=energy,
        estimated_cost_czk=energy * average,
    )


def _prepared() -> ExecutionLifecycleRecord:
    profile = _profile()
    policy = _policy()
    plan = _plan()
    attempt = ExecutionAttempt(
        attempt_id="attempt-1",
        entry_id="entry-1",
        profile_id=profile.profile_id,
        entity_id=profile.entity_id,
        approval_id="approval-1",
        approval_fingerprint="a" * 64,
        snapshot_digest=execution_snapshot_digest(profile, plan, policy),
        intent="execute_load_plan",
        approval_issued_at=int((START - timedelta(minutes=5)).timestamp()),
        approval_expires_at=int((START + timedelta(minutes=5)).timestamp()),
        created_at=int((START - timedelta(minutes=1)).timestamp()),
    ).validated()
    snapshot = ExecutionActionSnapshot.from_attempt_and_intent(
        attempt=attempt,
        intent=resolve_start_action_intent(profile),
        created_at=attempt.created_at,
    )
    readiness = evaluate_execution_readiness(
        attempt=attempt,
        snapshot=snapshot,
        profile=profile,
        plan=plan,
        policy=policy,
        current_state="off",
        now=START,
    )
    return ExecutionLifecycleRecord.prepared(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=plan,
        readiness=readiness,
        created_at=int(START.timestamp()),
    )


async def _repository_with_recovery() -> tuple[_FakeStore, ExecutionLifecycleRepository]:
    store = _FakeStore()
    repository = ExecutionLifecycleRepository(store)
    prepared = (await repository.async_prepare(_prepared())).record
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)
    await repository.async_update(dispatching)
    recovered = require_recovery_after_restart(
        dispatching,
        now=dispatching.updated_at + 1,
    )
    await repository.async_update(recovered)
    return store, repository


async def _repository_with_dispatched() -> tuple[_FakeStore, ExecutionLifecycleRepository]:
    store = _FakeStore()
    repository = ExecutionLifecycleRepository(store)
    prepared = (await repository.async_prepare(_prepared())).record
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)
    await repository.async_update(dispatching)
    dispatched = confirm_dispatch(dispatching, now=dispatching.updated_at + 1)
    await repository.async_update(dispatched)
    return store, repository


def _proof(ready: bool) -> StartStopOwnershipProof:
    return StartStopOwnershipProof(
        start_lifecycle_id=_prepared().lifecycle_id,
        stop_lease_present=ready,
        stop_lifecycle_present=ready,
        stop_lease_matches=ready,
        stop_lifecycle_matches=ready,
        ownership_ready=ready,
        reason="stop_ownership_ready" if ready else "stop_lifecycle_missing",
    )


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    repository: ExecutionLifecycleRepository,
    *,
    ownership_ready: bool = True,
) -> None:
    monkeypatch.setattr(
        verification,
        "lifecycle_repository",
        lambda hass, entry_id: repository,
    )
    monkeypatch.setattr(
        verification,
        "assert_lifecycle_recovery_ready",
        lambda hass, entry_id: None,
    )

    async def ownership_proof(hass, *, entry_id, start):
        return _proof(ownership_ready)

    monkeypatch.setattr(
        verification,
        "async_start_stop_ownership_proof",
        ownership_proof,
    )


@pytest.mark.asyncio
async def test_unknown_outcome_recovery_verifies_without_confirming_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repository = await _repository_with_recovery()
    _wire(monkeypatch, repository)
    hass = _FakeHass("on")
    before_saves = store.saves

    result = await verification.async_verify_recovery_lifecycle(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START + timedelta(seconds=10),
    )

    record = await repository.async_get_by_attempt_id("attempt-1")
    assert record is not None
    assert record.state == STATE_VERIFIED
    assert record.verification_status == VERIFY_CONFIRMED
    assert record.service_call_status == CALL_UNKNOWN
    assert record.as_dict()["service_call_performed"] is None
    assert result["stop_ownership"]["ownership_ready"] is True
    assert result["verification_performed"] is True
    assert result["state_transition_performed"] is True
    assert result["idempotent_replay"] is False
    assert result["can_redispatch"] is False
    assert result["service_call_performed"] is None
    assert result["execution_performed"] is False
    assert result["executor_available"] is False
    assert store.saves == before_saves + 1


@pytest.mark.asyncio
async def test_missing_stop_ownership_blocks_desired_state_verification_without_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repository = await _repository_with_recovery()
    _wire(monkeypatch, repository, ownership_ready=False)
    hass = _FakeHass("on")
    before = await repository.async_get_by_attempt_id("attempt-1")
    before_saves = store.saves

    with pytest.raises(verification.RecoveryVerificationError, match="durable_stop_ownership_missing"):
        await verification.async_verify_recovery_lifecycle(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START + timedelta(seconds=10),
        )

    after = await repository.async_get_by_attempt_id("attempt-1")
    assert after == before
    assert after is not None and after.state == STATE_RECOVERY_REQUIRED
    assert store.saves == before_saves


@pytest.mark.asyncio
async def test_confirmed_dispatch_verifies_and_preserves_confirmed_call_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, repository = await _repository_with_dispatched()
    _wire(monkeypatch, repository)
    hass = _FakeHass("on")

    result = await verification.async_verify_recovery_lifecycle(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START + timedelta(seconds=10),
    )

    record = await repository.async_get_by_attempt_id("attempt-1")
    assert record is not None
    assert record.state == STATE_VERIFIED
    assert record.service_call_status == CALL_CONFIRMED
    assert record.as_dict()["service_call_performed"] is True
    assert result["service_call_performed"] is True
    assert result["execution_performed"] is False
    assert result["can_redispatch"] is False


@pytest.mark.asyncio
async def test_mismatched_live_state_rejects_without_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repository = await _repository_with_recovery()
    _wire(monkeypatch, repository)
    hass = _FakeHass("off")
    before = await repository.async_get_by_attempt_id("attempt-1")
    before_saves = store.saves

    with pytest.raises(verification.RecoveryVerificationError, match="not safe"):
        await verification.async_verify_recovery_lifecycle(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START + timedelta(seconds=10),
        )

    after = await repository.async_get_by_attempt_id("attempt-1")
    assert after == before
    assert store.saves == before_saves


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [None, "unknown", "unavailable"])
async def test_unavailable_live_state_rejects_without_verification(
    monkeypatch: pytest.MonkeyPatch,
    state: str | None,
) -> None:
    _, repository = await _repository_with_recovery()
    _wire(monkeypatch, repository)

    with pytest.raises(verification.RecoveryVerificationError, match="not safe"):
        await verification.async_verify_recovery_lifecycle(
            _FakeHass(state),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START + timedelta(seconds=10),
        )

    record = await repository.async_get_by_attempt_id("attempt-1")
    assert record is not None
    assert record.state == STATE_RECOVERY_REQUIRED


@pytest.mark.asyncio
async def test_storage_failure_rolls_back_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repository = await _repository_with_recovery()
    _wire(monkeypatch, repository)
    hass = _FakeHass("on")
    before = await repository.async_get_by_attempt_id("attempt-1")
    store.fail_save = True

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await verification.async_verify_recovery_lifecycle(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START + timedelta(seconds=10),
        )

    after = await repository.async_get_by_attempt_id("attempt-1")
    assert after == before
    assert after is not None
    assert after.state == STATE_RECOVERY_REQUIRED
    assert after.service_call_status == CALL_UNKNOWN


@pytest.mark.asyncio
async def test_verified_retry_is_idempotent_even_if_live_state_later_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repository = await _repository_with_recovery()
    _wire(monkeypatch, repository)
    hass = _FakeHass("on")
    first = await verification.async_verify_recovery_lifecycle(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START + timedelta(seconds=10),
    )
    before_saves = store.saves
    hass.states = _FakeStates("off")

    retry = await verification.async_verify_recovery_lifecycle(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START + timedelta(minutes=5),
    )

    assert first["lifecycle"] == retry["lifecycle"]
    assert retry["verification_performed"] is False
    assert retry["state_transition_performed"] is False
    assert retry["idempotent_replay"] is True
    assert retry["desired_state_observed_now"] is False
    assert retry["service_call_performed"] is None
    assert retry["execution_performed"] is False
    assert store.saves == before_saves


@pytest.mark.asyncio
async def test_startup_recovery_gate_blocks_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass("on")

    def blocked(hass, entry_id):
        raise LifecycleRecoveryBlockedError("execution lifecycle recovery is failed")

    monkeypatch.setattr(verification, "assert_lifecycle_recovery_ready", blocked)

    with pytest.raises(LifecycleRecoveryBlockedError, match="recovery is failed"):
        await verification.async_verify_recovery_lifecycle(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START,
        )


@pytest.mark.asyncio
async def test_unknown_attempt_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _FakeStore()
    repository = ExecutionLifecycleRepository(store)
    _wire(monkeypatch, repository)

    with pytest.raises(verification.RecoveryVerificationError, match="not found"):
        await verification.async_verify_recovery_lifecycle(
            _FakeHass("on"),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="missing",
            now=START,
        )
