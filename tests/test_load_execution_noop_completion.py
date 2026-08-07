from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_noop_completion as noop
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_dispatch_gate import (
    DISPATCH_GATE_ALREADY_SATISFIED,
    DISPATCH_GATE_READY,
)
from custom_components.frakon_energy.load_execution_lifecycle import (
    CALL_NOT_STARTED,
    STATE_CANCELLED,
    STATE_DISPATCHING,
    VERIFY_PENDING,
    ExecutionLifecycleRecord,
    ExecutionLifecycleRepository,
    begin_dispatch,
)
from custom_components.frakon_energy.load_execution_lifecycle_recovery import (
    LifecycleRecoveryBlockedError,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import evaluate_execution_readiness
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


class _FakeHass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}


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


async def _repository() -> tuple[_FakeStore, ExecutionLifecycleRepository]:
    store = _FakeStore()
    repository = ExecutionLifecycleRepository(store)
    await repository.async_prepare(_prepared())
    return store, repository


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    repository: ExecutionLifecycleRepository,
    *,
    gate_status: str = DISPATCH_GATE_ALREADY_SATISFIED,
    gate_calls: list[int] | None = None,
) -> None:
    monkeypatch.setattr(noop, "lifecycle_repository", lambda hass, entry_id: repository)
    monkeypatch.setattr(noop, "assert_lifecycle_recovery_ready", lambda hass, entry_id: None)

    async def gate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if gate_calls is not None:
            gate_calls.append(1)
        return {
            "dispatch_gate": {
                "status": gate_status,
                "reason": "test",
                "can_dispatch": gate_status == DISPATCH_GATE_READY,
                "can_redispatch": False,
            }
        }

    monkeypatch.setattr(noop, "async_execution_dispatch_gate", gate)


@pytest.mark.asyncio
async def test_already_satisfied_terminally_closes_without_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repository = await _repository()
    _wire(monkeypatch, repository)
    before_saves = store.saves

    result = await noop.async_complete_already_satisfied_noop(
        _FakeHass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START,
    )

    record = await repository.async_get_by_attempt_id("attempt-1")
    assert record is not None
    assert record.state == STATE_CANCELLED
    assert record.failure_reason == noop.NOOP_TERMINAL_REASON
    assert record.service_call_status == CALL_NOT_STARTED
    assert record.verification_status == VERIFY_PENDING
    assert record.dispatch_attempts == 0
    assert record.as_dict()["service_call_performed"] is False
    assert result["noop_completed"] is True
    assert result["terminal_reason"] == noop.NOOP_TERMINAL_REASON
    assert result["state_transition_performed"] is True
    assert result["idempotent_replay"] is False
    assert result["can_dispatch"] is False
    assert result["can_redispatch"] is False
    assert result["service_call_performed"] is False
    assert result["execution_performed"] is False
    assert result["executor_available"] is False
    assert store.saves == before_saves + 1


@pytest.mark.asyncio
async def test_exact_noop_retry_is_inert_and_does_not_rerun_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repository = await _repository()
    gate_calls: list[int] = []
    _wire(monkeypatch, repository, gate_calls=gate_calls)
    hass = _FakeHass()
    first = await noop.async_complete_already_satisfied_noop(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START,
    )
    before_saves = store.saves

    retry = await noop.async_complete_already_satisfied_noop(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START + timedelta(minutes=10),
    )

    assert first["lifecycle"] == retry["lifecycle"]
    assert retry["noop_completed"] is True
    assert retry["state_transition_performed"] is False
    assert retry["idempotent_replay"] is True
    assert retry["service_call_performed"] is False
    assert len(gate_calls) == 1
    assert store.saves == before_saves


@pytest.mark.asyncio
async def test_ready_to_dispatch_cannot_be_closed_as_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repository = await _repository()
    _wire(monkeypatch, repository, gate_status=DISPATCH_GATE_READY)
    before = await repository.async_get_by_attempt_id("attempt-1")
    before_saves = store.saves

    with pytest.raises(noop.NoopCompletionError, match="does not allow no-op"):
        await noop.async_complete_already_satisfied_noop(
            _FakeHass(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START,
        )

    after = await repository.async_get_by_attempt_id("attempt-1")
    assert after == before
    assert store.saves == before_saves


@pytest.mark.asyncio
async def test_non_prepared_lifecycle_cannot_be_noop_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, repository = await _repository()
    prepared = await repository.async_get_by_attempt_id("attempt-1")
    assert prepared is not None
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)
    await repository.async_update(dispatching)
    _wire(monkeypatch, repository)

    with pytest.raises(noop.NoopCompletionError, match="not prepared"):
        await noop.async_complete_already_satisfied_noop(
            _FakeHass(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START,
        )

    current = await repository.async_get_by_attempt_id("attempt-1")
    assert current is not None
    assert current.state == STATE_DISPATCHING


@pytest.mark.asyncio
async def test_storage_failure_rolls_back_noop_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repository = await _repository()
    _wire(monkeypatch, repository)
    before = await repository.async_get_by_attempt_id("attempt-1")
    store.fail_save = True

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await noop.async_complete_already_satisfied_noop(
            _FakeHass(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START,
        )

    after = await repository.async_get_by_attempt_id("attempt-1")
    assert after == before
    assert after is not None
    assert after.dispatch_attempts == 0


@pytest.mark.asyncio
async def test_startup_recovery_gate_blocks_noop_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass()

    def blocked(hass, entry_id):
        raise LifecycleRecoveryBlockedError("execution lifecycle recovery is failed")

    monkeypatch.setattr(noop, "assert_lifecycle_recovery_ready", blocked)

    with pytest.raises(LifecycleRecoveryBlockedError, match="recovery is failed"):
        await noop.async_complete_already_satisfied_noop(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START,
        )


@pytest.mark.asyncio
async def test_missing_lifecycle_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _FakeStore()
    repository = ExecutionLifecycleRepository(store)
    _wire(monkeypatch, repository)

    with pytest.raises(noop.NoopCompletionError, match="not found"):
        await noop.async_complete_already_satisfied_noop(
            _FakeHass(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="missing",
            now=START,
        )
