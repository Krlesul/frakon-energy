from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_lifecycle_recovery as recovery
from custom_components.frakon_energy import load_execution_lifecycle_recovery_ws_api as recovery_ws
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import (
    STATE_DISPATCHED,
    STATE_RECOVERY_REQUIRED,
    ExecutionLifecycleRecord,
    ExecutionLifecycleRepository,
    begin_dispatch,
    confirm_dispatch,
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

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = data


class _FakeStates:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def get(self, entity_id: str) -> object | None:
        return SimpleNamespace(state=self.value) if self.value is not None else None


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
    energy = power * duration / 60
    return LoadPlan(
        load_id="ev-home",
        name="Enyaq",
        starts_at=START.isoformat(),
        ends_at=(START + timedelta(minutes=duration)).isoformat(),
        duration_minutes=duration,
        interval_count=8,
        power_kw=power,
        average_czk_kwh=2.0,
        minimum_czk_kwh=1.5,
        maximum_czk_kwh=2.5,
        estimated_energy_kwh=energy,
        estimated_cost_czk=energy * 2.0,
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


async def _recovered_repository() -> ExecutionLifecycleRepository:
    repository = ExecutionLifecycleRepository(_FakeStore())
    prepared = (await repository.async_prepare(_prepared())).record
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)
    await repository.async_update(dispatching)
    recovered = recovery.require_recovery_after_restart(
        dispatching,
        now=prepared.updated_at + 2,
    )
    await repository.async_update(recovered)
    return repository


async def _dispatched_repository() -> ExecutionLifecycleRepository:
    repository = ExecutionLifecycleRepository(_FakeStore())
    prepared = (await repository.async_prepare(_prepared())).record
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)
    await repository.async_update(dispatching)
    dispatched = confirm_dispatch(dispatching, now=prepared.updated_at + 2)
    await repository.async_update(dispatched)
    return repository


@pytest.mark.asyncio
async def test_recovery_diagnostics_observe_desired_state_without_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass("on")
    repository = await _recovered_repository()
    current = (await repository.async_list())[0]
    assert current.state == STATE_RECOVERY_REQUIRED
    monkeypatch.setattr(
        recovery_ws,
        "lifecycle_repository",
        lambda hass, entry_id: repository,
    )
    monkeypatch.setattr(
        recovery_ws,
        "lifecycle_recovery_summary",
        lambda hass, entry_id: recovery.LifecycleRecoverySummary(
            entry_id=entry_id,
            status=recovery.RECOVERY_OK,
            scanned=1,
            transitioned_to_recovery=1,
            recovery_required=1,
            dispatched_pending_verification=0,
        ),
    )

    result = await recovery_ws.async_lifecycle_recovery_diagnostics(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
    )
    after = (await repository.async_list())[0]

    assert result["recovery"]["status"] == recovery.RECOVERY_OK
    assert result["lifecycles"][0]["state"] == STATE_RECOVERY_REQUIRED
    assert result["lifecycles"][0]["desired_state_observed"] is True
    assert result["lifecycles"][0]["diagnostic"] == "desired_state_observed_after_unknown_dispatch"
    assert result["lifecycles"][0]["service_call_performed"] is None
    assert result["manual_review_required"] is False
    assert result["read_only"] is True
    assert result["state_transition_performed"] is False
    assert result["execution_performed"] is False
    assert result["executor_available"] is False
    assert after.state == STATE_RECOVERY_REQUIRED


@pytest.mark.asyncio
async def test_recovery_diagnostics_flag_manual_review_when_desired_state_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass("off")
    repository = await _recovered_repository()
    monkeypatch.setattr(
        recovery_ws,
        "lifecycle_repository",
        lambda hass, entry_id: repository,
    )
    monkeypatch.setattr(
        recovery_ws,
        "lifecycle_recovery_summary",
        lambda hass, entry_id: recovery.LifecycleRecoverySummary(
            entry_id=entry_id,
            status=recovery.RECOVERY_OK,
            scanned=1,
            transitioned_to_recovery=1,
            recovery_required=1,
            dispatched_pending_verification=0,
        ),
    )

    result = await recovery_ws.async_lifecycle_recovery_diagnostics(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert result["lifecycles"][0]["desired_state_observed"] is False
    assert result["lifecycles"][0]["diagnostic"] == "manual_recovery_review_required"
    assert result["manual_review_required"] is True
    assert result["state_transition_performed"] is False


@pytest.mark.asyncio
async def test_failed_startup_recovery_requires_manual_review_without_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass(None)
    repository = ExecutionLifecycleRepository(_FakeStore())
    monkeypatch.setattr(
        recovery_ws,
        "lifecycle_repository",
        lambda hass, entry_id: repository,
    )
    monkeypatch.setattr(
        recovery_ws,
        "lifecycle_recovery_summary",
        lambda hass, entry_id: recovery.LifecycleRecoverySummary(
            entry_id=entry_id,
            status=recovery.RECOVERY_FAILED,
            scanned=0,
            transitioned_to_recovery=0,
            recovery_required=0,
            dispatched_pending_verification=0,
            error="storage unavailable",
        ),
    )

    result = await recovery_ws.async_lifecycle_recovery_diagnostics(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert result["recovery"]["status"] == recovery.RECOVERY_FAILED
    assert result["lifecycles"] == []
    assert result["manual_review_required"] is True
    assert result["execution_performed"] is False
    assert result["executor_available"] is False


@pytest.mark.asyncio
async def test_dispatched_without_desired_state_requires_manual_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass("off")
    repository = await _dispatched_repository()
    current = (await repository.async_list())[0]
    assert current.state == STATE_DISPATCHED
    monkeypatch.setattr(
        recovery_ws,
        "lifecycle_repository",
        lambda hass, entry_id: repository,
    )
    monkeypatch.setattr(
        recovery_ws,
        "lifecycle_recovery_summary",
        lambda hass, entry_id: recovery.LifecycleRecoverySummary(
            entry_id=entry_id,
            status=recovery.RECOVERY_OK,
            scanned=1,
            transitioned_to_recovery=0,
            recovery_required=0,
            dispatched_pending_verification=1,
        ),
    )

    result = await recovery_ws.async_lifecycle_recovery_diagnostics(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert result["lifecycles"][0]["diagnostic"] == "dispatch_confirmed_but_desired_state_not_observed"
    assert result["manual_review_required"] is True
    assert result["state_transition_performed"] is False
    assert result["execution_performed"] is False
