from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_lifecycle_ws_api as lifecycle_ws
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import (
    ExecutionLifecycleRecord,
    ExecutionLifecycleRepository,
    begin_dispatch,
    require_recovery_after_restart,
    verify_desired_state,
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


@pytest.mark.asyncio
async def test_verified_recovery_replay_does_not_claim_confirmed_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ExecutionLifecycleRepository(_FakeStore())
    prepared = (await repository.async_prepare(_prepared())).record
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)
    await repository.async_update(dispatching)
    recovery = require_recovery_after_restart(dispatching, now=prepared.updated_at + 2)
    await repository.async_update(recovery)
    verified = verify_desired_state(
        recovery,
        current_state="on",
        now=prepared.updated_at + 3,
    )
    await repository.async_update(verified)

    monkeypatch.setattr(
        lifecycle_ws,
        "lifecycle_repository",
        lambda hass, entry_id: repository,
    )
    monkeypatch.setattr(
        lifecycle_ws,
        "assert_lifecycle_recovery_ready",
        lambda hass, entry_id: None,
    )

    result = await lifecycle_ws.async_prepare_execution_lifecycle(
        SimpleNamespace(data={}),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        plan_value=_plan().as_dict(),
        now=START + timedelta(minutes=10),
    )

    assert result["lifecycle"]["state"] == "verified"
    assert result["lifecycle"]["service_call_status"] == "unknown"
    assert result["service_call_performed"] is None
    assert result["execution_performed"] is False
    assert result["prepared_only"] is False
    assert result["executor_available"] is False
