from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_recovery_resolution_ws_api as resolution_ws
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import (
    STATE_RECOVERY_REQUIRED,
    ExecutionLifecycleRecord,
    ExecutionLifecycleRepository,
    begin_dispatch,
    require_recovery_after_restart,
)
from custom_components.frakon_energy.load_execution_lifecycle_recovery import (
    RECOVERY_OK,
    LifecycleRecoveryBlockedError,
    LifecycleRecoverySummary,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import evaluate_execution_readiness
from custom_components.frakon_energy.load_execution_recovery_resolution import (
    REASON_ENTITY_STATE_UNAVAILABLE,
    RESOLUTION_BLOCKED,
    RESOLUTION_SAFE_TO_VERIFY,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)


class _FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.saves = 0

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
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


async def _repository() -> ExecutionLifecycleRepository:
    repository = ExecutionLifecycleRepository(_FakeStore())
    prepared = (await repository.async_prepare(_prepared())).record
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)
    await repository.async_update(dispatching)
    recovered = require_recovery_after_restart(
        dispatching,
        now=dispatching.updated_at + 1,
    )
    await repository.async_update(recovered)
    return repository


def _recovery_summary() -> LifecycleRecoverySummary:
    return LifecycleRecoverySummary(
        entry_id="entry-1",
        status=RECOVERY_OK,
        scanned=1,
        transitioned_to_recovery=1,
        recovery_required=1,
        dispatched_pending_verification=0,
    )


def _allow_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        resolution_ws,
        "assert_lifecycle_recovery_ready",
        lambda hass, entry_id: None,
    )
    monkeypatch.setattr(
        resolution_ws,
        "lifecycle_recovery_summary",
        lambda hass, entry_id: _recovery_summary(),
    )


@pytest.mark.asyncio
async def test_resolution_endpoint_reads_live_state_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass("on")
    repository = await _repository()
    before = await repository.async_get_by_attempt_id("attempt-1")
    assert before is not None and before.state == STATE_RECOVERY_REQUIRED
    before_saves = repository._store.saves  # type: ignore[attr-defined]
    monkeypatch.setattr(
        resolution_ws,
        "lifecycle_repository",
        lambda hass, entry_id: repository,
    )
    _allow_recovery(monkeypatch)

    result = await resolution_ws.async_recovery_resolution_plan(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
    )

    after = await repository.async_get_by_attempt_id("attempt-1")
    assert result["resolution"]["status"] == RESOLUTION_SAFE_TO_VERIFY
    assert result["resolution"]["current_state"] == "on"
    assert result["resolution"]["can_mark_verified"] is True
    assert result["resolution"]["can_redispatch"] is False
    assert result["read_only"] is True
    assert result["resolution_performed"] is False
    assert result["state_transition_performed"] is False
    assert result["service_call_performed"] is False
    assert result["execution_performed"] is False
    assert result["executor_available"] is False
    assert after == before
    assert repository._store.saves == before_saves  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_resolution_endpoint_blocks_when_live_state_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass(None)
    repository = await _repository()
    monkeypatch.setattr(
        resolution_ws,
        "lifecycle_repository",
        lambda hass, entry_id: repository,
    )
    _allow_recovery(monkeypatch)

    result = await resolution_ws.async_recovery_resolution_plan(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
    )

    assert result["resolution"]["status"] == RESOLUTION_BLOCKED
    assert result["resolution"]["reason"] == REASON_ENTITY_STATE_UNAVAILABLE
    assert result["resolution"]["can_mark_verified"] is False
    assert result["resolution"]["can_redispatch"] is False
    assert result["resolution_performed"] is False


@pytest.mark.asyncio
async def test_resolution_endpoint_fails_closed_when_startup_recovery_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass("on")

    def blocked(hass, entry_id):
        raise LifecycleRecoveryBlockedError("execution lifecycle recovery is failed")

    monkeypatch.setattr(resolution_ws, "assert_lifecycle_recovery_ready", blocked)

    with pytest.raises(LifecycleRecoveryBlockedError, match="recovery is failed"):
        await resolution_ws.async_recovery_resolution_plan(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
        )


@pytest.mark.asyncio
async def test_resolution_endpoint_rejects_unknown_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass("on")
    repository = ExecutionLifecycleRepository(_FakeStore())
    monkeypatch.setattr(
        resolution_ws,
        "lifecycle_repository",
        lambda hass, entry_id: repository,
    )
    _allow_recovery(monkeypatch)

    with pytest.raises(resolution_ws.RecoveryResolutionLookupError, match="not found"):
        await resolution_ws.async_recovery_resolution_plan(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="missing",
        )
