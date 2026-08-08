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
from custom_components.frakon_energy.load_execution_lifecycle import (
    STATE_PREPARED,
    ExecutionPlanSnapshot,
)
from custom_components.frakon_energy.load_execution_pending_run import ExecutionPendingRun
from custom_components.frakon_energy.load_execution_pending_run_scheduler import (
    STATUS_MISSED,
    STATUS_PREPARED_WITH_STOP_LEASE,
    STATUS_SCHEDULED,
    ExecutionPendingRunScheduler,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_GENERIC, LoadProfile

START = datetime(2026, 8, 8, 14, 0, tzinfo=timezone.utc)


class _PendingRepo:
    def __init__(self, records: list[ExecutionPendingRun]) -> None:
        self.records = records
        self.fail_list = False

    async def async_list(self):
        if self.fail_list:
            raise RuntimeError("pending run store unavailable")
        return tuple(self.records)

    async def async_get_by_attempt_id(self, attempt_id: str):
        return next((item for item in self.records if item.attempt_id == attempt_id), None)


class _LifecycleRepo:
    def __init__(self, record: Any = None) -> None:
        self.record = record

    async def async_get_by_attempt_id(self, attempt_id: str):
        return self.record


class _Hass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.tasks: list[asyncio.Task[Any]] = []

    def async_create_task(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task


def _profile() -> LoadProfile:
    return LoadProfile(
        "test-load",
        "Test load",
        PROFILE_KIND_GENERIC,
        60,
        2.0,
        entity_id="switch.test_load",
    )


def _pending() -> ExecutionPendingRun:
    attempt = ExecutionAttempt(
        attempt_id="attempt-1",
        entry_id="entry-1",
        profile_id="test-load",
        entity_id="switch.test_load",
        approval_id="approval-1",
        approval_fingerprint="a" * 64,
        snapshot_digest="b" * 64,
        intent="execute_load_plan",
        approval_issued_at=100,
        approval_expires_at=300,
        created_at=150,
    ).validated()
    snapshot = ExecutionActionSnapshot.from_attempt_and_intent(
        attempt=attempt,
        intent=resolve_start_action_intent(_profile()),
        created_at=attempt.created_at,
    )
    plan = LoadPlan(
        load_id="test-load",
        name="Test load",
        starts_at=START.isoformat(),
        ends_at=(START + timedelta(hours=1)).isoformat(),
        duration_minutes=60,
        interval_count=4,
        power_kw=2.0,
        average_czk_kwh=2.0,
        minimum_czk_kwh=1.0,
        maximum_czk_kwh=3.0,
        estimated_energy_kwh=2.0,
        estimated_cost_czk=4.0,
    )
    return ExecutionPendingRun.from_records(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=ExecutionPlanSnapshot.from_load_plan(plan),
        created_at=160,
    )


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    pending_repo: _PendingRepo,
    lifecycle_repo: _LifecycleRepo,
    *,
    start_healthy: bool = True,
    stop_healthy: bool = True,
) -> None:
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
    monkeypatch.setattr(
        scheduler_mod,
        "lifecycle_recovery_summary",
        lambda hass, entry_id: SimpleNamespace(status="ok"),
    )
    monkeypatch.setattr(
        scheduler_mod,
        "stop_recovery_summary",
        lambda hass, entry_id: SimpleNamespace(status="ok"),
    )
    monkeypatch.setattr(
        scheduler_mod,
        "start_scheduler",
        lambda hass, entry_id: SimpleNamespace(started=True, healthy=start_healthy),
    )
    monkeypatch.setattr(
        scheduler_mod,
        "stop_scheduler",
        lambda hass, entry_id: SimpleNamespace(started=True, healthy=stop_healthy),
    )


@pytest.mark.asyncio
async def test_future_pending_run_registers_exact_start_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _pending()
    pending_repo = _PendingRepo([record])
    lifecycle_repo = _LifecycleRepo()
    _wire(monkeypatch, pending_repo, lifecycle_repo)
    timers: list[tuple[Any, datetime]] = []

    def track(hass, action, when):
        timers.append((action, when))
        return lambda: None

    monkeypatch.setattr(scheduler_mod, "async_track_point_in_utc_time", track)
    scheduler = ExecutionPendingRunScheduler(_Hass(), "entry-1")  # type: ignore[arg-type]
    scheduler._started = True

    await scheduler.async_refresh(now=START - timedelta(minutes=10))

    assert len(timers) == 1
    assert timers[0][1] == START
    status = scheduler.statuses()[0]
    assert status.status == STATUS_SCHEDULED
    assert status.timer_active is True
    assert status.next_wake_at == START.isoformat()
    assert status.service_call_performed is False


@pytest.mark.asyncio
async def test_due_timer_prepares_lifecycle_then_exact_stop_lease_without_direct_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _pending()
    pending_repo = _PendingRepo([record])
    lifecycle_repo = _LifecycleRepo()
    _wire(monkeypatch, pending_repo, lifecycle_repo)
    hass = _Hass()
    timers: list[tuple[Any, datetime]] = []
    order: list[str] = []

    def track(hass_obj, action, when):
        timers.append((action, when))
        return lambda: None

    async def prepare_lifecycle(hass_obj, *, entry_id, attempt_id, plan_value, now):
        order.append("lifecycle")
        assert plan_value == record.plan.as_dict()
        lifecycle_repo.record = SimpleNamespace(state=STATE_PREPARED)
        return {"prepared_only": True, "service_call_performed": False}

    async def prepare_stop(hass_obj, *, entry_id, attempt_id, now):
        order.append("stop_lease")
        return {
            "stop_obligation_armed": True,
            "service_call_performed": False,
            "execution_performed": False,
        }

    monkeypatch.setattr(scheduler_mod, "async_track_point_in_utc_time", track)
    monkeypatch.setattr(scheduler_mod, "async_prepare_execution_lifecycle", prepare_lifecycle)
    monkeypatch.setattr(scheduler_mod, "async_prepare_stop_lease", prepare_stop)
    scheduler = ExecutionPendingRunScheduler(hass, "entry-1")  # type: ignore[arg-type]
    scheduler._started = True

    await scheduler.async_refresh(now=START - timedelta(minutes=1))
    assert timers
    timers[0][0](START)
    await asyncio.gather(*hass.tasks)

    assert order == ["lifecycle", "stop_lease"]
    status = scheduler.statuses()[0]
    assert status.status == STATUS_PREPARED_WITH_STOP_LEASE
    assert status.lifecycle_prepared is True
    assert status.stop_lease_prepared is True
    assert status.service_call_performed is False
    assert not hasattr(hass, "services")


@pytest.mark.asyncio
async def test_restart_inside_start_grace_processes_pending_run_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _pending()
    pending_repo = _PendingRepo([record])
    lifecycle_repo = _LifecycleRepo()
    _wire(monkeypatch, pending_repo, lifecycle_repo)
    calls: list[str] = []

    async def prepare_lifecycle(*args: Any, **kwargs: Any):
        calls.append("lifecycle")
        lifecycle_repo.record = SimpleNamespace(state=STATE_PREPARED)
        return {"prepared_only": True, "service_call_performed": False}

    async def prepare_stop(*args: Any, **kwargs: Any):
        calls.append("stop_lease")
        return {"stop_obligation_armed": True, "service_call_performed": False}

    monkeypatch.setattr(scheduler_mod, "async_prepare_execution_lifecycle", prepare_lifecycle)
    monkeypatch.setattr(scheduler_mod, "async_prepare_stop_lease", prepare_stop)
    monkeypatch.setattr(
        scheduler_mod,
        "async_track_point_in_utc_time",
        lambda *args, **kwargs: pytest.fail("no future timer expected inside grace"),
    )
    scheduler = ExecutionPendingRunScheduler(_Hass(), "entry-1")  # type: ignore[arg-type]
    scheduler._started = True

    await scheduler.async_refresh(now=START + timedelta(seconds=1))

    assert scheduler.started is True
    assert scheduler.healthy is True
    assert calls == ["lifecycle", "stop_lease"]
    assert scheduler.statuses()[0].status == STATUS_PREPARED_WITH_STOP_LEASE


@pytest.mark.asyncio
async def test_restart_after_missed_start_window_never_prepares_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _pending()
    pending_repo = _PendingRepo([record])
    lifecycle_repo = _LifecycleRepo()
    _wire(monkeypatch, pending_repo, lifecycle_repo)
    prepare_calls = 0

    async def prepare_lifecycle(*args: Any, **kwargs: Any):
        nonlocal prepare_calls
        prepare_calls += 1
        return {}

    monkeypatch.setattr(scheduler_mod, "async_prepare_execution_lifecycle", prepare_lifecycle)
    scheduler = ExecutionPendingRunScheduler(_Hass(), "entry-1")  # type: ignore[arg-type]
    scheduler._started = True

    await scheduler.async_refresh(now=START + timedelta(seconds=31))

    assert prepare_calls == 0
    assert scheduler.statuses()[0].status == STATUS_MISSED
    assert scheduler.statuses()[0].service_call_performed is False


@pytest.mark.asyncio
async def test_restart_crash_gap_with_prepared_lifecycle_only_adds_stop_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _pending()
    pending_repo = _PendingRepo([record])
    lifecycle_repo = _LifecycleRepo(SimpleNamespace(state=STATE_PREPARED))
    _wire(monkeypatch, pending_repo, lifecycle_repo)
    lifecycle_prepare_calls = 0
    stop_prepare_calls = 0

    async def prepare_lifecycle(*args: Any, **kwargs: Any):
        nonlocal lifecycle_prepare_calls
        lifecycle_prepare_calls += 1
        return {}

    async def prepare_stop(*args: Any, **kwargs: Any):
        nonlocal stop_prepare_calls
        stop_prepare_calls += 1
        return {"stop_obligation_armed": True, "service_call_performed": False}

    monkeypatch.setattr(scheduler_mod, "async_prepare_execution_lifecycle", prepare_lifecycle)
    monkeypatch.setattr(scheduler_mod, "async_prepare_stop_lease", prepare_stop)
    scheduler = ExecutionPendingRunScheduler(_Hass(), "entry-1")  # type: ignore[arg-type]
    scheduler._started = True

    await scheduler.async_refresh(now=START + timedelta(seconds=1))

    assert lifecycle_prepare_calls == 0
    assert stop_prepare_calls == 1
    assert scheduler.statuses()[0].status == STATUS_PREPARED_WITH_STOP_LEASE


@pytest.mark.asyncio
async def test_unhealthy_start_or_stop_runtime_blocks_scheduler_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _pending()
    pending_repo = _PendingRepo([record])
    lifecycle_repo = _LifecycleRepo()
    _wire(monkeypatch, pending_repo, lifecycle_repo, start_healthy=False)
    scheduler = ExecutionPendingRunScheduler(_Hass(), "entry-1")  # type: ignore[arg-type]
    scheduler._started = True

    await scheduler.async_refresh(now=START - timedelta(minutes=1))

    assert scheduler.healthy is False
    assert scheduler.last_error == "autonomous_start_runtime_not_ready"
    assert scheduler.statuses() == ()


@pytest.mark.asyncio
async def test_startup_pending_store_failure_marks_scheduler_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending_repo = _PendingRepo([_pending()])
    pending_repo.fail_list = True
    lifecycle_repo = _LifecycleRepo()
    _wire(monkeypatch, pending_repo, lifecycle_repo)
    scheduler = ExecutionPendingRunScheduler(_Hass(), "entry-1")  # type: ignore[arg-type]

    await scheduler.async_start()

    assert scheduler.started is True
    assert scheduler.healthy is False
    assert "pending run store unavailable" in str(scheduler.last_error)
