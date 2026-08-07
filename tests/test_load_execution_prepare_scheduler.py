import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from custom_components.frakon_energy import load_execution_prepare_scheduler as scheduler_mod
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import (
    ExecutionLifecycleRecord,
    ExecutionLifecycleRepository,
)
from custom_components.frakon_energy.load_execution_lifecycle_recovery import (
    RECOVERY_FAILED,
    RECOVERY_OK,
    LifecycleRecoverySummary,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import evaluate_execution_readiness
from custom_components.frakon_energy.load_execution_schedule import (
    ExecutionSchedule,
    ExecutionScheduleRepository,
)
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


class _FakeHass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.tasks: list[asyncio.Task[Any]] = []

    def async_create_task(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task


def _profile() -> LoadProfile:
    return LoadProfile("ev-home", "Enyaq", PROFILE_KIND_EV, 120, 11.0, entity_id="switch.enyaq_charging")


def _policy() -> LoadExecutionPolicy:
    return LoadExecutionPolicy("ev-home", mode=EXECUTION_MODE_APPROVAL_REQUIRED, max_power_kw=11.0, max_duration_minutes=120)


def _plan() -> LoadPlan:
    return LoadPlan(
        load_id="ev-home",
        name="Enyaq",
        starts_at=START.isoformat(),
        ends_at=(START + timedelta(minutes=120)).isoformat(),
        duration_minutes=120,
        interval_count=8,
        power_kw=11.0,
        average_czk_kwh=2.0,
        minimum_czk_kwh=1.5,
        maximum_czk_kwh=2.5,
        estimated_energy_kwh=22.0,
        estimated_cost_czk=44.0,
    )


def _attempt() -> ExecutionAttempt:
    return ExecutionAttempt(
        attempt_id="attempt-1",
        entry_id="entry-1",
        profile_id="ev-home",
        entity_id="switch.enyaq_charging",
        approval_id="approval-1",
        approval_fingerprint="a" * 64,
        snapshot_digest=execution_snapshot_digest(_profile(), _plan(), _policy()),
        intent="execute_load_plan",
        approval_issued_at=int((START - timedelta(minutes=10)).timestamp()),
        approval_expires_at=int((START + timedelta(minutes=5)).timestamp()),
        created_at=int((START - timedelta(minutes=5)).timestamp()),
    ).validated()


def _snapshot() -> ExecutionActionSnapshot:
    attempt = _attempt()
    return ExecutionActionSnapshot.from_attempt_and_intent(
        attempt=attempt,
        intent=resolve_start_action_intent(_profile()),
        created_at=attempt.created_at,
    )


def _schedule() -> ExecutionSchedule:
    attempt = _attempt()
    snapshot = _snapshot()
    readiness = evaluate_execution_readiness(
        attempt=attempt,
        snapshot=snapshot,
        profile=_profile(),
        plan=_plan(),
        policy=_policy(),
        current_state="off",
        now=START - timedelta(minutes=2),
    )
    return ExecutionSchedule.from_approved_readiness(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=_plan(),
        readiness=readiness,
        created_at=int((START - timedelta(minutes=2)).timestamp()),
    )


def _lifecycle() -> ExecutionLifecycleRecord:
    attempt = _attempt()
    snapshot = _snapshot()
    readiness = evaluate_execution_readiness(
        attempt=attempt,
        snapshot=snapshot,
        profile=_profile(),
        plan=_plan(),
        policy=_policy(),
        current_state="off",
        now=START,
    )
    return ExecutionLifecycleRecord.prepared(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=_plan(),
        readiness=readiness,
        created_at=int(START.timestamp()),
    )


async def _install_repositories(
    monkeypatch: pytest.MonkeyPatch,
    *,
    include_lifecycle: bool = False,
) -> tuple[ExecutionScheduleRepository, ExecutionLifecycleRepository]:
    schedules = ExecutionScheduleRepository(_FakeStore())
    lifecycles = ExecutionLifecycleRepository(_FakeStore())
    await schedules.async_record(_schedule())
    if include_lifecycle:
        await lifecycles.async_prepare(_lifecycle())
    monkeypatch.setattr(scheduler_mod, "schedule_repository", lambda hass, entry_id: schedules)
    monkeypatch.setattr(scheduler_mod, "lifecycle_repository", lambda hass, entry_id: lifecycles)
    return schedules, lifecycles


def _recovery(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    monkeypatch.setattr(
        scheduler_mod,
        "lifecycle_recovery_summary",
        lambda hass, entry_id: LifecycleRecoverySummary(
            entry_id=entry_id,
            status=status,
            scanned=0,
            transitioned_to_recovery=0,
            recovery_required=0,
            dispatched_pending_verification=0,
            error="recovery failed" if status == RECOVERY_FAILED else None,
        ),
    )


def _track(monkeypatch: pytest.MonkeyPatch):
    registrations: list[dict[str, Any]] = []

    def fake_track(hass: Any, action: Callable[[datetime], None], point: datetime) -> Callable[[], None]:
        item = {"action": action, "point": point, "cancelled": False}
        registrations.append(item)

        def unsubscribe() -> None:
            item["cancelled"] = True

        return unsubscribe

    monkeypatch.setattr(scheduler_mod, "async_track_point_in_utc_time", fake_track)
    return registrations


def _prepare_success(monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]], *, state: str = "prepared", service_call: bool | None = False) -> None:
    async def fake_prepare(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "entry_id": "entry-1",
            "schedule": _schedule().as_dict(),
            "lifecycle": {
                "lifecycle": {"state": state},
                "created": state == "prepared",
                "idempotent_replay": state != "prepared",
                "prepared_only": state == "prepared",
                "execution_performed": service_call is True,
                "service_call_performed": service_call,
                "executor_available": False,
            },
            "persisted_plan_used": True,
            "execution_performed": service_call is True,
            "service_call_performed": service_call,
            "executor_available": False,
        }

    monkeypatch.setattr(scheduler_mod, "async_prepare_scheduled_execution", fake_prepare)


@pytest.mark.asyncio
async def test_future_schedule_registers_one_timer_at_exact_utc_start(monkeypatch: pytest.MonkeyPatch) -> None:
    await _install_repositories(monkeypatch)
    _recovery(monkeypatch, RECOVERY_OK)
    registrations = _track(monkeypatch)
    calls: list[dict[str, Any]] = []
    _prepare_success(monkeypatch, calls)
    hass = _FakeHass()
    scheduler = scheduler_mod.ExecutionPrepareScheduler(hass, "entry-1")  # type: ignore[arg-type]

    await scheduler.async_start(now=START - timedelta(minutes=1))

    assert len(registrations) == 1
    assert registrations[0]["point"] == START.astimezone(timezone.utc)
    status = scheduler.statuses()[0]
    assert status.status == scheduler_mod.STATUS_SCHEDULED
    assert status.timer_active is True
    assert status.next_wake_at == START.astimezone(timezone.utc).isoformat()
    assert calls == []


@pytest.mark.asyncio
async def test_timer_fires_prepare_scheduled_once(monkeypatch: pytest.MonkeyPatch) -> None:
    await _install_repositories(monkeypatch)
    _recovery(monkeypatch, RECOVERY_OK)
    registrations = _track(monkeypatch)
    calls: list[dict[str, Any]] = []
    _prepare_success(monkeypatch, calls)
    hass = _FakeHass()
    scheduler = scheduler_mod.ExecutionPrepareScheduler(hass, "entry-1")  # type: ignore[arg-type]
    await scheduler.async_start(now=START - timedelta(minutes=1))

    registrations[0]["action"](START.astimezone(timezone.utc))
    await asyncio.gather(*hass.tasks)

    assert len(calls) == 1
    assert calls[0]["entry_id"] == "entry-1"
    assert calls[0]["attempt_id"] == "attempt-1"
    assert calls[0]["now"] == START.astimezone(timezone.utc)
    status = scheduler.statuses()[0]
    assert status.status == scheduler_mod.STATUS_PREPARED
    assert status.lifecycle_state == "prepared"
    assert status.timer_active is False
    assert status.execution_performed is False
    assert status.service_call_performed is False


@pytest.mark.asyncio
async def test_start_window_prepares_immediately_without_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    await _install_repositories(monkeypatch)
    _recovery(monkeypatch, RECOVERY_OK)
    registrations = _track(monkeypatch)
    calls: list[dict[str, Any]] = []
    _prepare_success(monkeypatch, calls)
    hass = _FakeHass()
    scheduler = scheduler_mod.ExecutionPrepareScheduler(hass, "entry-1")  # type: ignore[arg-type]

    await scheduler.async_start(now=START)

    assert registrations == []
    assert len(calls) == 1
    assert scheduler.statuses()[0].status == scheduler_mod.STATUS_PREPARED


@pytest.mark.asyncio
async def test_recovery_failure_blocks_all_timers_and_prepare(monkeypatch: pytest.MonkeyPatch) -> None:
    await _install_repositories(monkeypatch)
    _recovery(monkeypatch, RECOVERY_FAILED)
    registrations = _track(monkeypatch)
    calls: list[dict[str, Any]] = []
    _prepare_success(monkeypatch, calls)
    scheduler = scheduler_mod.ExecutionPrepareScheduler(_FakeHass(), "entry-1")  # type: ignore[arg-type]

    await scheduler.async_start(now=START - timedelta(minutes=1))

    assert registrations == []
    assert calls == []
    assert scheduler.statuses()[0].status == scheduler_mod.STATUS_BLOCKED_RECOVERY


@pytest.mark.asyncio
async def test_existing_lifecycle_suppresses_timer_and_prepare(monkeypatch: pytest.MonkeyPatch) -> None:
    await _install_repositories(monkeypatch, include_lifecycle=True)
    _recovery(monkeypatch, RECOVERY_OK)
    registrations = _track(monkeypatch)
    calls: list[dict[str, Any]] = []
    _prepare_success(monkeypatch, calls)
    scheduler = scheduler_mod.ExecutionPrepareScheduler(_FakeHass(), "entry-1")  # type: ignore[arg-type]

    await scheduler.async_start(now=START - timedelta(minutes=1))

    assert registrations == []
    assert calls == []
    status = scheduler.statuses()[0]
    assert status.status == scheduler_mod.STATUS_LIFECYCLE_EXISTS
    assert status.lifecycle_state == "prepared"


@pytest.mark.asyncio
async def test_missed_schedule_never_prepares(monkeypatch: pytest.MonkeyPatch) -> None:
    await _install_repositories(monkeypatch)
    _recovery(monkeypatch, RECOVERY_OK)
    registrations = _track(monkeypatch)
    calls: list[dict[str, Any]] = []
    _prepare_success(monkeypatch, calls)
    scheduler = scheduler_mod.ExecutionPrepareScheduler(_FakeHass(), "entry-1")  # type: ignore[arg-type]

    await scheduler.async_start(now=START + timedelta(seconds=31))

    assert registrations == []
    assert calls == []
    assert scheduler.statuses()[0].status == scheduler_mod.STATUS_MISSED


@pytest.mark.asyncio
async def test_prepare_rejection_is_recorded_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    await _install_repositories(monkeypatch)
    _recovery(monkeypatch, RECOVERY_OK)
    _track(monkeypatch)
    calls: list[dict[str, Any]] = []

    async def fake_prepare(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        raise ValueError("entity unavailable")

    monkeypatch.setattr(scheduler_mod, "async_prepare_scheduled_execution", fake_prepare)
    scheduler = scheduler_mod.ExecutionPrepareScheduler(_FakeHass(), "entry-1")  # type: ignore[arg-type]

    await scheduler.async_start(now=START)

    assert len(calls) == 1
    status = scheduler.statuses()[0]
    assert status.status == scheduler_mod.STATUS_REJECTED
    assert status.last_error == "entity unavailable"
    assert status.timer_active is False


@pytest.mark.asyncio
async def test_unknown_service_call_evidence_remains_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    await _install_repositories(monkeypatch)
    _recovery(monkeypatch, RECOVERY_OK)
    _track(monkeypatch)
    calls: list[dict[str, Any]] = []
    _prepare_success(monkeypatch, calls, state="verified", service_call=None)
    scheduler = scheduler_mod.ExecutionPrepareScheduler(_FakeHass(), "entry-1")  # type: ignore[arg-type]

    await scheduler.async_start(now=START)

    status = scheduler.statuses()[0]
    assert status.status == scheduler_mod.STATUS_LIFECYCLE_EXISTS
    assert status.lifecycle_state == "verified"
    assert status.service_call_performed is None
    assert status.execution_performed is False


@pytest.mark.asyncio
async def test_stop_cancels_registered_timers(monkeypatch: pytest.MonkeyPatch) -> None:
    await _install_repositories(monkeypatch)
    _recovery(monkeypatch, RECOVERY_OK)
    registrations = _track(monkeypatch)
    _prepare_success(monkeypatch, [])
    scheduler = scheduler_mod.ExecutionPrepareScheduler(_FakeHass(), "entry-1")  # type: ignore[arg-type]
    await scheduler.async_start(now=START - timedelta(minutes=1))

    await scheduler.async_stop()

    assert scheduler.started is False
    assert registrations[0]["cancelled"] is True
