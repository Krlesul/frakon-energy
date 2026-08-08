from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_pending_run_scheduler as scheduler_mod
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import ExecutionPlanSnapshot
from custom_components.frakon_energy.load_execution_pending_run import ExecutionPendingRun
from custom_components.frakon_energy.load_execution_pending_run_scheduler import (
    STATUS_ERROR,
    STATUS_NO_START_NEEDED,
    ExecutionPendingRunScheduler,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_GENERIC, LoadProfile

START = datetime(2026, 8, 8, 16, 0, tzinfo=timezone.utc)


class _PendingRepo:
    def __init__(self, record: ExecutionPendingRun) -> None:
        self.record = record

    async def async_list(self):
        return (self.record,)

    async def async_get_by_attempt_id(self, attempt_id: str):
        return self.record if attempt_id == self.record.attempt_id else None


class _LifecycleRepo:
    async def async_get_by_attempt_id(self, attempt_id: str):
        return None


class _Hass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.tasks: list[asyncio.Task[Any]] = []

    def async_create_task(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task


def _pending() -> ExecutionPendingRun:
    profile = LoadProfile(
        "already-on-load",
        "Already on load",
        PROFILE_KIND_GENERIC,
        60,
        0.1,
        entity_id="input_boolean.already_on_load",
    )
    attempt = ExecutionAttempt(
        attempt_id="attempt-already-on",
        entry_id="entry-1",
        profile_id=profile.profile_id,
        entity_id=profile.entity_id,
        approval_id="approval-already-on",
        approval_fingerprint="a" * 64,
        snapshot_digest="b" * 64,
        intent="execute_load_plan",
        approval_issued_at=100,
        approval_expires_at=300,
        created_at=150,
    ).validated()
    snapshot = ExecutionActionSnapshot.from_attempt_and_intent(
        attempt=attempt,
        intent=resolve_start_action_intent(profile),
        created_at=attempt.created_at,
    )
    plan = LoadPlan(
        load_id=profile.profile_id,
        name=profile.name,
        starts_at=START.isoformat(),
        ends_at=(START + timedelta(hours=1)).isoformat(),
        duration_minutes=60,
        interval_count=4,
        power_kw=0.1,
        average_czk_kwh=2.0,
        minimum_czk_kwh=1.0,
        maximum_czk_kwh=3.0,
        estimated_energy_kwh=0.1,
        estimated_cost_czk=0.2,
    )
    return ExecutionPendingRun.from_records(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=ExecutionPlanSnapshot.from_load_plan(plan),
        created_at=160,
    )


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    record: ExecutionPendingRun,
) -> tuple[_PendingRepo, _LifecycleRepo]:
    pending_repo = _PendingRepo(record)
    lifecycle_repo = _LifecycleRepo()
    monkeypatch.setattr(
        scheduler_mod,
        "pending_run_repository",
        lambda hass, entry_id: pending_repo,
    )
    monkeypatch.setattr(
        scheduler_mod,
        "lifecycle_repository",
        lambda hass, entry_id: lifecycle_repo,
    )
    ok = SimpleNamespace(status="ok")
    monkeypatch.setattr(
        scheduler_mod,
        "lifecycle_recovery_summary",
        lambda hass, entry_id: ok,
    )
    monkeypatch.setattr(
        scheduler_mod,
        "stop_recovery_summary",
        lambda hass, entry_id: ok,
    )
    monkeypatch.setattr(
        scheduler_mod,
        "start_scheduler",
        lambda hass, entry_id: SimpleNamespace(started=True, healthy=True),
    )
    monkeypatch.setattr(
        scheduler_mod,
        "stop_scheduler",
        lambda hass, entry_id: SimpleNamespace(started=True, healthy=True),
    )
    return pending_repo, lifecycle_repo


@pytest.mark.asyncio
async def test_already_satisfied_prepare_rejection_becomes_clean_no_start_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _pending()
    _wire(monkeypatch, record)
    lifecycle_prepare_calls = 0
    stop_lease_calls = 0
    readiness_calls = 0

    async def prepare_lifecycle(*args: Any, **kwargs: Any):
        nonlocal lifecycle_prepare_calls
        lifecycle_prepare_calls += 1
        raise ValueError("execution is not ready to prepare: already_satisfied")

    async def readiness(*args: Any, **kwargs: Any):
        nonlocal readiness_calls
        readiness_calls += 1
        assert kwargs["attempt_id"] == record.attempt_id
        assert kwargs["plan_value"] == record.plan.as_dict()
        return {
            "readiness": {
                "status": "already_satisfied",
                "reason": "desired_state_already_observed",
            },
            "service_call_performed": False,
            "execution_performed": False,
        }

    async def prepare_stop(*args: Any, **kwargs: Any):
        nonlocal stop_lease_calls
        stop_lease_calls += 1
        return {}

    monkeypatch.setattr(
        scheduler_mod,
        "async_prepare_execution_lifecycle",
        prepare_lifecycle,
    )
    monkeypatch.setattr(scheduler_mod, "async_execution_readiness", readiness)
    monkeypatch.setattr(scheduler_mod, "async_prepare_stop_lease", prepare_stop)
    scheduler = ExecutionPendingRunScheduler(_Hass(), "entry-1")  # type: ignore[arg-type]
    scheduler._started = True

    await scheduler.async_refresh(now=START)

    assert lifecycle_prepare_calls == 1
    assert readiness_calls == 1
    assert stop_lease_calls == 0
    status = scheduler.statuses()[0]
    assert status.status == STATUS_NO_START_NEEDED
    assert status.timer_active is False
    assert status.lifecycle_prepared is False
    assert status.stop_lease_prepared is False
    assert status.retry_count == 0
    assert status.last_error is None
    assert status.service_call_performed is False


@pytest.mark.asyncio
async def test_non_already_satisfied_readiness_never_masks_prepare_failure_as_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _pending()
    _wire(monkeypatch, record)
    stop_lease_calls = 0

    async def prepare_lifecycle(*args: Any, **kwargs: Any):
        raise RuntimeError("lifecycle store unavailable")

    async def readiness(*args: Any, **kwargs: Any):
        return {
            "readiness": {
                "status": "ready",
                "reason": "all_execution_guards_passed",
            }
        }

    async def prepare_stop(*args: Any, **kwargs: Any):
        nonlocal stop_lease_calls
        stop_lease_calls += 1
        return {}

    monkeypatch.setattr(
        scheduler_mod,
        "async_prepare_execution_lifecycle",
        prepare_lifecycle,
    )
    monkeypatch.setattr(scheduler_mod, "async_execution_readiness", readiness)
    monkeypatch.setattr(scheduler_mod, "async_prepare_stop_lease", prepare_stop)
    hass = _Hass()
    scheduler = ExecutionPendingRunScheduler(hass, "entry-1")  # type: ignore[arg-type]
    scheduler._started = True

    await scheduler.async_refresh(now=START)

    assert stop_lease_calls == 0
    status = scheduler.statuses()[0]
    assert status.status == STATUS_ERROR
    assert status.last_error == "lifecycle store unavailable"
    assert status.service_call_performed is False
    assert not hasattr(hass, "services")
