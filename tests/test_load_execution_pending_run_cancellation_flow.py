from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_lifecycle_runtime as lifecycle_runtime
from custom_components.frakon_energy import load_execution_lifecycle_ws_api as lifecycle_ws
from custom_components.frakon_energy import load_execution_pending_run_cancellation as cancellation_mod
from custom_components.frakon_energy import load_execution_pending_run_runtime as pending_runtime
from custom_components.frakon_energy import load_execution_pending_run_scheduler as scheduler_mod
from custom_components.frakon_energy import load_execution_pending_run_ws_api as pending_ws
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import ExecutionPlanSnapshot
from custom_components.frakon_energy.load_execution_pending_run import (
    ExecutionPendingRun,
    ExecutionPendingRunRepository,
)
from custom_components.frakon_energy.load_execution_pending_run_cancellation import (
    PendingRunCancellation,
    PendingRunCancellationError,
    PendingRunCancellationRepository,
    async_cancel_pending_run_before_lifecycle,
)
from custom_components.frakon_energy.load_execution_pending_run_scheduler import (
    STATUS_CANCELLED,
    ExecutionPendingRunScheduler,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_GENERIC, LoadProfile

NOW = datetime(2026, 8, 8, 15, 0, tzinfo=timezone.utc)
START = NOW + timedelta(hours=1)


class _Store:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = data


class _Hass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.tasks: list[Any] = []

    def async_create_task(self, coro):
        self.tasks.append(coro)
        return coro


class _LifecycleRepo:
    def __init__(self, record: Any = None) -> None:
        self.record = record
        self.prepare_calls = 0

    async def async_get_by_attempt_id(self, attempt_id: str):
        return self.record

    async def async_prepare(self, record):
        self.prepare_calls += 1
        self.record = record
        return SimpleNamespace(
            record=record,
            as_dict=lambda: {
                "lifecycle": record.as_dict(),
                "created": True,
                "idempotent_replay": False,
            },
        )


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
    profile = _profile()
    plan = LoadPlan(
        load_id=profile.profile_id,
        name=profile.name,
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
    attempt = ExecutionAttempt(
        attempt_id="attempt-1",
        entry_id="entry-1",
        profile_id=profile.profile_id,
        entity_id=profile.entity_id,
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
        intent=resolve_start_action_intent(profile),
        created_at=attempt.created_at,
    )
    return ExecutionPendingRun.from_records(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=ExecutionPlanSnapshot.from_load_plan(plan),
        created_at=160,
    )


@pytest.mark.asyncio
async def test_cancel_tombstone_blocks_lifecycle_prepare_before_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    pending = _pending()
    pending_repo = ExecutionPendingRunRepository(_Store())
    cancellation_repo = PendingRunCancellationRepository(_Store())
    lifecycle_repo = _LifecycleRepo()
    await pending_repo.async_record(pending)

    monkeypatch.setattr(cancellation_mod, "cancellation_repository", lambda hass, entry_id: cancellation_repo)
    monkeypatch.setattr(pending_runtime, "pending_run_repository", lambda hass, entry_id: pending_repo)
    monkeypatch.setattr(lifecycle_runtime, "lifecycle_repository", lambda hass, entry_id: lifecycle_repo)

    result = await async_cancel_pending_run_before_lifecycle(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id=pending.attempt_id,
        cancelled_by="admin-1",
        now=NOW,
    )

    assert result.created is True
    assert result.cancellation.pending_run_id == pending.pending_run_id
    assert lifecycle_repo.record is None
    assert not hasattr(hass, "services")

    monkeypatch.setattr(lifecycle_ws, "cancellation_repository", lambda hass, entry_id: cancellation_repo)
    monkeypatch.setattr(lifecycle_ws, "lifecycle_repository", lambda hass, entry_id: lifecycle_repo)
    monkeypatch.setattr(lifecycle_ws, "assert_lifecycle_recovery_ready", lambda hass, entry_id: None)

    async def unexpected_readiness(*args: Any, **kwargs: Any):
        pytest.fail("readiness must not run after durable cancellation")

    monkeypatch.setattr(lifecycle_ws.readiness_ws, "async_execution_readiness", unexpected_readiness)

    with pytest.raises(lifecycle_ws.LifecyclePrepareError, match="durably cancelled"):
        await lifecycle_ws.async_prepare_execution_lifecycle(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id=pending.attempt_id,
            plan_value=pending.plan.as_dict(),
            now=START,
        )

    assert lifecycle_repo.prepare_calls == 0


@pytest.mark.asyncio
async def test_cancel_is_rejected_when_lifecycle_won_the_guard_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    pending = _pending()
    pending_repo = ExecutionPendingRunRepository(_Store())
    cancellation_repo = PendingRunCancellationRepository(_Store())
    lifecycle_repo = _LifecycleRepo(SimpleNamespace(state="prepared"))
    await pending_repo.async_record(pending)

    monkeypatch.setattr(cancellation_mod, "cancellation_repository", lambda hass, entry_id: cancellation_repo)
    monkeypatch.setattr(pending_runtime, "pending_run_repository", lambda hass, entry_id: pending_repo)
    monkeypatch.setattr(lifecycle_runtime, "lifecycle_repository", lambda hass, entry_id: lifecycle_repo)

    with pytest.raises(PendingRunCancellationError, match="after a durable lifecycle exists"):
        await async_cancel_pending_run_before_lifecycle(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id=pending.attempt_id,
            cancelled_by="admin-1",
            now=NOW,
        )

    assert await cancellation_repo.async_list() == ()
    assert not hasattr(hass, "services")


@pytest.mark.asyncio
async def test_restart_scheduler_marks_cancelled_without_registering_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    pending = _pending()
    pending_repo = ExecutionPendingRunRepository(_Store())
    cancellation_repo = PendingRunCancellationRepository(_Store())
    lifecycle_repo = _LifecycleRepo()
    await pending_repo.async_record(pending)
    cancellation = PendingRunCancellation.from_pending_run(
        pending,
        cancelled_at=int(NOW.timestamp()),
        cancelled_by="admin-1",
    )
    await cancellation_repo.async_record(cancellation)

    monkeypatch.setattr(scheduler_mod, "pending_run_repository", lambda hass, entry_id: pending_repo)
    monkeypatch.setattr(scheduler_mod, "cancellation_repository", lambda hass, entry_id: cancellation_repo)
    monkeypatch.setattr(scheduler_mod, "lifecycle_repository", lambda hass, entry_id: lifecycle_repo)
    monkeypatch.setattr(scheduler_mod, "lifecycle_recovery_summary", lambda hass, entry_id: SimpleNamespace(status="ok"))
    monkeypatch.setattr(scheduler_mod, "stop_recovery_summary", lambda hass, entry_id: SimpleNamespace(status="ok"))
    monkeypatch.setattr(scheduler_mod, "start_scheduler", lambda hass, entry_id: SimpleNamespace(started=True, healthy=True))
    monkeypatch.setattr(scheduler_mod, "stop_scheduler", lambda hass, entry_id: SimpleNamespace(started=True, healthy=True))
    monkeypatch.setattr(
        scheduler_mod,
        "async_track_point_in_utc_time",
        lambda *args, **kwargs: pytest.fail("cancelled pending run must not register a timer"),
    )

    scheduler = ExecutionPendingRunScheduler(hass, "entry-1")  # type: ignore[arg-type]
    scheduler._started = True
    await scheduler.async_refresh(now=NOW)

    statuses = scheduler.statuses()
    assert len(statuses) == 1
    assert statuses[0].status == STATUS_CANCELLED
    assert statuses[0].timer_active is False
    assert statuses[0].service_call_performed is False
    assert not hasattr(hass, "services")


@pytest.mark.asyncio
async def test_cancelled_attempt_cannot_be_rescheduled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    pending = _pending()
    cancellation_repo = PendingRunCancellationRepository(_Store())
    await cancellation_repo.async_record(
        PendingRunCancellation.from_pending_run(
            pending,
            cancelled_at=int(NOW.timestamp()),
            cancelled_by="admin-1",
        )
    )

    monkeypatch.setattr(pending_ws, "assert_lifecycle_recovery_ready", lambda hass, entry_id: None)
    monkeypatch.setattr(pending_ws, "cancellation_repository", lambda hass, entry_id: cancellation_repo)

    with pytest.raises(pending_ws.PendingRunPrepareError, match="cannot be rescheduled"):
        await pending_ws.async_create_pending_run(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id=pending.attempt_id,
            plan_value=pending.plan.as_dict(),
            now=NOW,
        )

    assert not hasattr(hass, "services")
