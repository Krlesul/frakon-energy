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
    STATE_RECOVERY_REQUIRED,
    ExecutionPlanSnapshot,
)
from custom_components.frakon_energy.load_execution_pending_run import ExecutionPendingRun
from custom_components.frakon_energy.load_execution_pending_run_scheduler import (
    STATUS_ERROR,
    STATUS_PREPARED_WITH_STOP_LEASE,
    STATUS_RETRYING_STOP_LEASE,
    STOP_LEASE_RETRY_SECONDS,
    ExecutionPendingRunScheduler,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_GENERIC, LoadProfile

START = datetime(2026, 8, 8, 16, 0, tzinfo=timezone.utc)
GRACE_END = START + timedelta(seconds=30)


class _PendingRepo:
    def __init__(self, record: ExecutionPendingRun) -> None:
        self.record = record

    async def async_list(self):
        return (self.record,)

    async def async_get_by_attempt_id(self, attempt_id: str):
        return self.record if attempt_id == self.record.attempt_id else None


class _LifecycleRepo:
    def __init__(self, state: str = STATE_PREPARED) -> None:
        self.record = SimpleNamespace(state=state)

    async def async_get_by_attempt_id(self, attempt_id: str):
        return self.record


class _CancellationRepo:
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
        "retry-load",
        "Retry load",
        PROFILE_KIND_GENERIC,
        60,
        0.1,
        entity_id="input_boolean.retry_load",
    )
    attempt = ExecutionAttempt(
        attempt_id="attempt-retry",
        entry_id="entry-1",
        profile_id=profile.profile_id,
        entity_id=profile.entity_id,
        approval_id="approval-retry",
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
    pending_repo: _PendingRepo,
    lifecycle_repo: _LifecycleRepo,
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
        "cancellation_repository",
        lambda hass, entry_id: _CancellationRepo(),
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

    async def unexpected_lifecycle_prepare(*args: Any, **kwargs: Any):
        pytest.fail("existing prepared lifecycle must not be prepared again")

    monkeypatch.setattr(
        scheduler_mod,
        "async_prepare_execution_lifecycle",
        unexpected_lifecycle_prepare,
    )


@pytest.mark.asyncio
async def test_transient_stop_lease_failure_retries_once_after_five_seconds_and_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _pending()
    pending_repo = _PendingRepo(record)
    lifecycle_repo = _LifecycleRepo()
    _wire(monkeypatch, pending_repo, lifecycle_repo)
    hass = _Hass()
    timers: list[tuple[Any, datetime]] = []
    stop_calls = 0

    def track(hass_obj, action, when):
        timers.append((action, when))
        return lambda: None

    async def prepare_stop(*args: Any, **kwargs: Any):
        nonlocal stop_calls
        stop_calls += 1
        if stop_calls == 1:
            raise RuntimeError("transient stop lease store failure")
        return {
            "stop_obligation_armed": True,
            "service_call_performed": False,
            "execution_performed": False,
        }

    monkeypatch.setattr(scheduler_mod, "async_track_point_in_utc_time", track)
    monkeypatch.setattr(scheduler_mod, "async_prepare_stop_lease", prepare_stop)
    scheduler = ExecutionPendingRunScheduler(hass, "entry-1")  # type: ignore[arg-type]
    scheduler._started = True

    await scheduler.async_refresh(now=START)

    assert stop_calls == 1
    assert len(timers) == 1
    assert timers[-1][1] == START + timedelta(seconds=STOP_LEASE_RETRY_SECONDS)
    first = scheduler.statuses()[0]
    assert first.status == STATUS_RETRYING_STOP_LEASE
    assert first.timer_active is True
    assert first.lifecycle_prepared is True
    assert first.stop_lease_prepared is False
    assert first.retry_count == 1
    assert "transient stop lease store failure" in str(first.last_error)
    assert first.service_call_performed is False

    timers[-1][0](START + timedelta(seconds=STOP_LEASE_RETRY_SECONDS))
    await asyncio.gather(*hass.tasks)

    assert stop_calls == 2
    final = scheduler.statuses()[0]
    assert final.status == STATUS_PREPARED_WITH_STOP_LEASE
    assert final.stop_lease_prepared is True
    assert final.retry_count == 0
    assert final.timer_active is False
    assert final.service_call_performed is False
    assert not hasattr(hass, "services")


@pytest.mark.asyncio
async def test_failure_that_moves_lifecycle_out_of_prepared_never_schedules_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _pending()
    pending_repo = _PendingRepo(record)
    lifecycle_repo = _LifecycleRepo()
    _wire(monkeypatch, pending_repo, lifecycle_repo)
    timers: list[tuple[Any, datetime]] = []

    monkeypatch.setattr(
        scheduler_mod,
        "async_track_point_in_utc_time",
        lambda hass, action, when: (timers.append((action, when)) or (lambda: None)),
    )

    async def prepare_stop(*args: Any, **kwargs: Any):
        lifecycle_repo.record = SimpleNamespace(state=STATE_RECOVERY_REQUIRED)
        raise RuntimeError("start outcome became uncertain during downstream refresh")

    monkeypatch.setattr(scheduler_mod, "async_prepare_stop_lease", prepare_stop)
    scheduler = ExecutionPendingRunScheduler(_Hass(), "entry-1")  # type: ignore[arg-type]
    scheduler._started = True

    await scheduler.async_refresh(now=START)

    assert timers == []
    status = scheduler.statuses()[0]
    assert status.status == STATUS_ERROR
    assert status.timer_active is False
    assert status.lifecycle_prepared is False
    assert status.retry_count == 0
    assert "uncertain" in str(status.last_error)


@pytest.mark.asyncio
async def test_failure_at_exact_grace_deadline_never_extends_execution_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _pending()
    pending_repo = _PendingRepo(record)
    lifecycle_repo = _LifecycleRepo()
    _wire(monkeypatch, pending_repo, lifecycle_repo)
    timers: list[tuple[Any, datetime]] = []

    monkeypatch.setattr(
        scheduler_mod,
        "async_track_point_in_utc_time",
        lambda hass, action, when: (timers.append((action, when)) or (lambda: None)),
    )

    async def prepare_stop(*args: Any, **kwargs: Any):
        raise RuntimeError("stop lease still unavailable at deadline")

    monkeypatch.setattr(scheduler_mod, "async_prepare_stop_lease", prepare_stop)
    scheduler = ExecutionPendingRunScheduler(_Hass(), "entry-1")  # type: ignore[arg-type]
    scheduler._started = True

    await scheduler.async_refresh(now=GRACE_END)

    assert timers == []
    status = scheduler.statuses()[0]
    assert status.status == STATUS_ERROR
    assert status.timer_active is False
    assert status.retry_count == 0
    assert status.lifecycle_prepared is True
    assert "deadline" in str(status.last_error)
