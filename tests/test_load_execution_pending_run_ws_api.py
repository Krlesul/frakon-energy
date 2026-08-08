from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_pending_run_scheduler as scheduler_mod
from custom_components.frakon_energy import load_execution_pending_run_ws_api as pending_ws
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_pending_run import (
    ExecutionPendingRunRepository,
    PendingRunConflictError,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import evaluate_execution_readiness
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_GENERIC, LoadProfile

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
START = NOW + timedelta(hours=1)


class _Store:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = data


class _LifecycleRepo:
    def __init__(self, record: Any = None) -> None:
        self.record = record

    async def async_get_by_attempt_id(self, attempt_id: str):
        return self.record


class _Hass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}


def _profile() -> LoadProfile:
    return LoadProfile(
        "test-load",
        "Test load",
        PROFILE_KIND_GENERIC,
        60,
        2.0,
        entity_id="switch.test_load",
    )


def _policy() -> LoadExecutionPolicy:
    return LoadExecutionPolicy(
        "test-load",
        mode=EXECUTION_MODE_APPROVAL_REQUIRED,
        max_power_kw=2.0,
        max_duration_minutes=60,
    )


def _plan(*, starts_at: datetime = START) -> LoadPlan:
    return LoadPlan(
        load_id="test-load",
        name="Test load",
        starts_at=starts_at.isoformat(),
        ends_at=(starts_at + timedelta(hours=1)).isoformat(),
        duration_minutes=60,
        interval_count=4,
        power_kw=2.0,
        average_czk_kwh=2.0,
        minimum_czk_kwh=1.0,
        maximum_czk_kwh=3.0,
        estimated_energy_kwh=2.0,
        estimated_cost_czk=4.0,
    )


def _attempt_and_snapshot(plan: LoadPlan) -> tuple[ExecutionAttempt, ExecutionActionSnapshot]:
    profile = _profile()
    policy = _policy()
    attempt = ExecutionAttempt(
        attempt_id="attempt-1",
        entry_id="entry-1",
        profile_id=profile.profile_id,
        entity_id=profile.entity_id,
        approval_id="approval-1",
        approval_fingerprint="a" * 64,
        snapshot_digest=execution_snapshot_digest(profile, plan, policy),
        intent="execute_load_plan",
        approval_issued_at=int((NOW - timedelta(minutes=1)).timestamp()),
        approval_expires_at=int((NOW + timedelta(minutes=5)).timestamp()),
        created_at=int(NOW.timestamp()),
    ).validated()
    snapshot = ExecutionActionSnapshot.from_attempt_and_intent(
        attempt=attempt,
        intent=resolve_start_action_intent(profile),
        created_at=attempt.created_at,
    )
    return attempt, snapshot


def _readiness_payload(plan: LoadPlan, *, now: datetime = NOW) -> dict[str, Any]:
    profile = _profile()
    policy = _policy()
    attempt, snapshot = _attempt_and_snapshot(plan)
    decision = evaluate_execution_readiness(
        attempt=attempt,
        snapshot=snapshot,
        profile=profile,
        plan=plan,
        policy=policy,
        current_state="off",
        now=now,
    )
    return {
        "entry_id": "entry-1",
        "attempt": attempt.as_dict(),
        "action_snapshot": snapshot.as_dict(),
        "profile": profile.as_dict(),
        "policy": policy.as_dict(),
        "plan": plan.as_dict(),
        "readiness": decision.as_dict(),
        "read_only": True,
        "execution_performed": False,
        "service_call_performed": False,
        "executor_available": False,
    }


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    pending_repo: ExecutionPendingRunRepository,
    lifecycle_repo: _LifecycleRepo,
) -> list[str]:
    refreshes: list[str] = []
    monkeypatch.setattr(
        pending_ws,
        "pending_run_repository",
        lambda hass, entry_id: pending_repo,
    )
    monkeypatch.setattr(
        pending_ws,
        "lifecycle_repository",
        lambda hass, entry_id: lifecycle_repo,
    )
    monkeypatch.setattr(
        pending_ws,
        "assert_lifecycle_recovery_ready",
        lambda hass, entry_id: None,
    )

    async def refresh(hass, entry_id):
        refreshes.append(entry_id)

    monkeypatch.setattr(
        scheduler_mod,
        "async_refresh_pending_run_scheduler_if_started",
        refresh,
    )
    return refreshes


@pytest.mark.asyncio
async def test_waiting_consumed_attempt_can_be_persisted_as_inert_pending_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    pending_repo = ExecutionPendingRunRepository(_Store())
    lifecycle_repo = _LifecycleRepo()
    refreshes = _wire(monkeypatch, pending_repo, lifecycle_repo)
    payload = _readiness_payload(plan)

    async def readiness(*args: Any, **kwargs: Any):
        return payload

    monkeypatch.setattr(pending_ws, "async_execution_readiness", readiness)

    result = await pending_ws.async_create_pending_run(
        _Hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        plan_value=plan.as_dict(),
        now=NOW,
    )

    assert result["created"] is True
    assert result["scheduled_only"] is True
    assert result["readiness_at_schedule"]["status"] == "waiting"
    assert result["service_call_performed"] is False
    assert result["execution_performed"] is False
    assert result["pending_run"]["plan"] == plan.as_dict()
    assert refreshes == ["entry-1"]


@pytest.mark.asyncio
async def test_blocked_readiness_is_never_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    pending_repo = ExecutionPendingRunRepository(_Store())
    lifecycle_repo = _LifecycleRepo()
    _wire(monkeypatch, pending_repo, lifecycle_repo)
    payload = _readiness_payload(plan)
    payload["readiness"] = {
        **payload["readiness"],
        "status": "blocked",
        "reason": "policy_not_eligible",
        "action_required": False,
    }

    async def readiness(*args: Any, **kwargs: Any):
        return payload

    monkeypatch.setattr(pending_ws, "async_execution_readiness", readiness)

    with pytest.raises(pending_ws.PendingRunPrepareError, match="not schedulable"):
        await pending_ws.async_create_pending_run(
            _Hass(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            plan_value=plan.as_dict(),
            now=NOW,
        )

    assert await pending_repo.async_list() == ()


@pytest.mark.asyncio
async def test_exact_retry_returns_persisted_pending_run_without_revalidating_stale_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    pending_repo = ExecutionPendingRunRepository(_Store())
    lifecycle_repo = _LifecycleRepo()
    _wire(monkeypatch, pending_repo, lifecycle_repo)
    payload = _readiness_payload(plan)
    readiness_calls = 0

    async def readiness(*args: Any, **kwargs: Any):
        nonlocal readiness_calls
        readiness_calls += 1
        return payload

    monkeypatch.setattr(pending_ws, "async_execution_readiness", readiness)
    first = await pending_ws.async_create_pending_run(
        _Hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        plan_value=plan.as_dict(),
        now=NOW,
    )
    replay = await pending_ws.async_create_pending_run(
        _Hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        plan_value=plan.as_dict(),
        now=START + timedelta(hours=2),
    )

    assert first["created"] is True
    assert replay["created"] is False
    assert replay["idempotent_replay"] is True
    assert replay["pending_run"] == first["pending_run"]
    assert readiness_calls == 1


@pytest.mark.asyncio
async def test_existing_lifecycle_blocks_first_pending_run_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    pending_repo = ExecutionPendingRunRepository(_Store())
    lifecycle_repo = _LifecycleRepo(SimpleNamespace(state="prepared"))
    _wire(monkeypatch, pending_repo, lifecycle_repo)
    readiness_calls = 0

    async def readiness(*args: Any, **kwargs: Any):
        nonlocal readiness_calls
        readiness_calls += 1
        return _readiness_payload(plan)

    monkeypatch.setattr(pending_ws, "async_execution_readiness", readiness)

    with pytest.raises(pending_ws.PendingRunPrepareError, match="already has a durable lifecycle"):
        await pending_ws.async_create_pending_run(
            _Hass(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            plan_value=plan.as_dict(),
            now=NOW,
        )

    assert readiness_calls == 0
    assert await pending_repo.async_list() == ()


@pytest.mark.asyncio
async def test_changed_plan_for_existing_pending_run_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    pending_repo = ExecutionPendingRunRepository(_Store())
    lifecycle_repo = _LifecycleRepo()
    _wire(monkeypatch, pending_repo, lifecycle_repo)
    payload = _readiness_payload(plan)

    async def readiness(*args: Any, **kwargs: Any):
        return payload

    monkeypatch.setattr(pending_ws, "async_execution_readiness", readiness)
    await pending_ws.async_create_pending_run(
        _Hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        plan_value=plan.as_dict(),
        now=NOW,
    )

    changed = _plan(starts_at=START + timedelta(hours=1))
    with pytest.raises(PendingRunConflictError, match="different plan snapshot"):
        await pending_ws.async_create_pending_run(
            _Hass(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            plan_value=changed.as_dict(),
            now=NOW,
        )
