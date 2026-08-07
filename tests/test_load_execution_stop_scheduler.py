import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_stop_scheduler as scheduler_mod
from custom_components.frakon_energy.load_execution_stop_lifecycle import (
    STOP_CALL_UNKNOWN,
    STOP_STATE_RECOVERY_REQUIRED,
    ExecutionStopLifecycleRecord,
)
from custom_components.frakon_energy.load_execution_stop_scheduler import (
    STATUS_BLOCKED,
    STATUS_READY_TO_STOP,
    STATUS_SATISFIED,
    STATUS_SCHEDULED,
    STATUS_VERIFIED,
    ExecutionStopScheduler,
)

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)
END = START + timedelta(hours=2)


class _States:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def get(self, entity_id: str) -> object | None:
        return SimpleNamespace(state=self.value) if self.value is not None else None


class _Hass:
    def __init__(self, state: str | None = "on") -> None:
        self.data: dict[str, Any] = {}
        self.states = _States(state)
        self.tasks: list[asyncio.Task[Any]] = []

    def async_create_task(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task


class _Repo:
    def __init__(self, records: list[ExecutionStopLifecycleRecord]) -> None:
        self.records = records
        self.fail_list = False

    async def async_list(self):
        if self.fail_list:
            raise RuntimeError("stop store unavailable")
        return tuple(self.records)

    async def async_get_by_start_lifecycle_id(self, start_lifecycle_id: str):
        return next(
            (r for r in self.records if r.start_lifecycle_id == start_lifecycle_id),
            None,
        )


def _owned() -> ExecutionStopLifecycleRecord:
    return ExecutionStopLifecycleRecord(
        stop_lifecycle_id="e" * 32,
        lease_id="a" * 32,
        entry_id="entry-1",
        start_lifecycle_id="b" * 32,
        attempt_id="attempt-1",
        action_snapshot_id="c" * 32,
        profile_id="ev-home",
        entity_id="switch.enyaq_charging",
        approval_snapshot_digest="d" * 64,
        plan_digest="f" * 64,
        starts_at=START.isoformat(),
        ends_at=END.isoformat(),
        service_domain="switch",
        service_name="turn_off",
        desired_state="off",
        state="owned",
        service_call_status="not_started",
        verification_status="pending",
        created_at=int(START.timestamp()),
        updated_at=int(START.timestamp()),
    ).validated()


def _recovery_required() -> ExecutionStopLifecycleRecord:
    return replace(
        _owned(),
        state=STOP_STATE_RECOVERY_REQUIRED,
        service_call_status=STOP_CALL_UNKNOWN,
        dispatch_attempts=1,
        dispatch_started_at=int(END.timestamp()),
        updated_at=int(END.timestamp()),
    ).validated()


def _patch_common(
    monkeypatch: pytest.MonkeyPatch,
    repo: _Repo,
    *,
    recovery_status: str = "ok",
):
    monkeypatch.setattr(
        scheduler_mod,
        "stop_lifecycle_repository",
        lambda hass, entry_id: repo,
    )
    monkeypatch.setattr(
        scheduler_mod,
        "stop_recovery_summary",
        lambda hass, entry_id: SimpleNamespace(status=recovery_status),
    )


@pytest.mark.asyncio
async def test_scheduler_registers_exact_end_timer_for_waiting_owned_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _owned()
    repo = _Repo([record])
    _patch_common(monkeypatch, repo)
    hass = _Hass("on")
    timers: list[tuple[Any, datetime]] = []
    cancelled: list[bool] = []

    def track(hass, action, when):
        timers.append((action, when))
        return lambda: cancelled.append(True)

    monkeypatch.setattr(scheduler_mod, "async_track_point_in_utc_time", track)
    scheduler = ExecutionStopScheduler(hass, "entry-1")  # type: ignore[arg-type]
    scheduler._started = True

    await scheduler.async_refresh(now=START)

    status = scheduler.statuses()[0]
    assert status.status == STATUS_SCHEDULED
    assert status.timer_active is True
    assert status.next_wake_at == END.astimezone(timezone.utc).isoformat()
    assert timers[0][1] == END.astimezone(timezone.utc)
    assert status.dispatch_required is False


@pytest.mark.asyncio
async def test_due_on_is_only_surfaced_ready_to_stop_without_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _Repo([_owned()])
    _patch_common(monkeypatch, repo)
    hass = _Hass("on")
    monkeypatch.setattr(
        scheduler_mod,
        "async_track_point_in_utc_time",
        lambda hass, action, when: lambda: None,
    )
    calls = {"noop": 0, "verify": 0}

    async def noop(*args, **kwargs):
        calls["noop"] += 1

    async def verify(*args, **kwargs):
        calls["verify"] += 1

    monkeypatch.setattr(scheduler_mod, "async_complete_stop_noop", noop)
    monkeypatch.setattr(scheduler_mod, "async_verify_stop_resolution", verify)
    scheduler = ExecutionStopScheduler(hass, "entry-1")  # type: ignore[arg-type]
    scheduler._started = True

    await scheduler.async_refresh(now=END)

    status = scheduler.statuses()[0]
    assert status.status == STATUS_READY_TO_STOP
    assert status.dispatch_required is True
    assert status.service_call_performed is False
    assert status.execution_performed is False
    assert calls == {"noop": 0, "verify": 0}


@pytest.mark.asyncio
async def test_due_off_auto_completes_only_safe_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _Repo([_owned()])
    _patch_common(monkeypatch, repo)
    hass = _Hass("off")
    monkeypatch.setattr(
        scheduler_mod,
        "async_track_point_in_utc_time",
        lambda hass, action, when: lambda: None,
    )
    called: list[str] = []

    async def noop(hass, *, entry_id, start_lifecycle_id, now):
        called.append(start_lifecycle_id)
        return {
            "resolution_performed": True,
            "service_call_performed": False,
        }

    monkeypatch.setattr(scheduler_mod, "async_complete_stop_noop", noop)
    scheduler = ExecutionStopScheduler(hass, "entry-1")  # type: ignore[arg-type]
    scheduler._started = True

    await scheduler.async_refresh(now=END)

    status = scheduler.statuses()[0]
    assert called == [_owned().start_lifecycle_id]
    assert status.status == STATUS_SATISFIED
    assert status.resolution_performed is True
    assert status.service_call_performed is False
    assert status.dispatch_required is False


@pytest.mark.asyncio
async def test_recovered_unknown_stop_off_auto_verifies_without_claiming_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _Repo([_recovery_required()])
    _patch_common(monkeypatch, repo)
    hass = _Hass("off")
    monkeypatch.setattr(
        scheduler_mod,
        "async_track_point_in_utc_time",
        lambda hass, action, when: lambda: None,
    )

    async def verify(hass, *, entry_id, start_lifecycle_id, now):
        return {
            "resolution_performed": True,
            "service_call_performed": None,
        }

    monkeypatch.setattr(scheduler_mod, "async_verify_stop_resolution", verify)
    scheduler = ExecutionStopScheduler(hass, "entry-1")  # type: ignore[arg-type]
    scheduler._started = True

    await scheduler.async_refresh(now=END + timedelta(seconds=5))

    status = scheduler.statuses()[0]
    assert status.status == STATUS_VERIFIED
    assert status.resolution_performed is True
    assert status.service_call_performed is None
    assert status.execution_performed is False


@pytest.mark.asyncio
async def test_unhealthy_recovery_blocks_timers_and_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _Repo([_owned()])
    _patch_common(monkeypatch, repo, recovery_status="failed")
    hass = _Hass("on")
    timer_calls: list[datetime] = []
    monkeypatch.setattr(
        scheduler_mod,
        "async_track_point_in_utc_time",
        lambda hass, action, when: timer_calls.append(when) or (lambda: None),
    )
    scheduler = ExecutionStopScheduler(hass, "entry-1")  # type: ignore[arg-type]
    scheduler._started = True

    await scheduler.async_refresh(now=START)

    assert timer_calls == []
    assert scheduler.statuses()[0].status == STATUS_BLOCKED
    assert scheduler.statuses()[0].dispatch_required is False


@pytest.mark.asyncio
async def test_timer_fire_processes_safe_noop_but_never_calls_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _Repo([_owned()])
    _patch_common(monkeypatch, repo)
    hass = _Hass("on")
    captured: dict[str, Any] = {}

    def track(hass, action, when):
        captured["action"] = action
        captured["when"] = when
        return lambda: None

    monkeypatch.setattr(scheduler_mod, "async_track_point_in_utc_time", track)

    async def noop(hass, *, entry_id, start_lifecycle_id, now):
        return {
            "resolution_performed": True,
            "service_call_performed": False,
        }

    monkeypatch.setattr(scheduler_mod, "async_complete_stop_noop", noop)
    scheduler = ExecutionStopScheduler(hass, "entry-1")  # type: ignore[arg-type]
    await scheduler.async_start()
    await scheduler.async_refresh(now=START)
    hass.states.value = "off"

    captured["action"](END)
    await asyncio.gather(*hass.tasks)

    status = scheduler.statuses()[0]
    assert status.status == STATUS_SATISFIED
    assert status.service_call_performed is False
    assert status.execution_performed is False


@pytest.mark.asyncio
async def test_unload_cancels_timer_and_queued_callback_cannot_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _Repo([_owned()])
    _patch_common(monkeypatch, repo)
    hass = _Hass("off")
    cancelled: list[bool] = []
    captured: dict[str, Any] = {}

    def track(hass, action, when):
        captured["action"] = action
        return lambda: cancelled.append(True)

    monkeypatch.setattr(scheduler_mod, "async_track_point_in_utc_time", track)
    calls: list[str] = []

    async def noop(*args, **kwargs):
        calls.append("noop")
        return {"resolution_performed": True, "service_call_performed": False}

    monkeypatch.setattr(scheduler_mod, "async_complete_stop_noop", noop)
    scheduler = ExecutionStopScheduler(hass, "entry-1")  # type: ignore[arg-type]
    await scheduler.async_start()
    await scheduler.async_refresh(now=START)
    await scheduler.async_stop()

    captured["action"](END)
    if hass.tasks:
        await asyncio.gather(*hass.tasks)

    assert cancelled
    assert calls == []
    assert scheduler.started is False


@pytest.mark.asyncio
async def test_scheduler_startup_store_failure_is_fail_closed_not_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _Repo([_owned()])
    repo.fail_list = True
    _patch_common(monkeypatch, repo)
    hass = _Hass("on")
    monkeypatch.setattr(
        scheduler_mod,
        "async_track_point_in_utc_time",
        lambda hass, action, when: lambda: None,
    )
    scheduler = ExecutionStopScheduler(hass, "entry-1")  # type: ignore[arg-type]

    await scheduler.async_start()

    assert scheduler.started is True
    assert scheduler.healthy is False
    assert "stop store unavailable" in str(scheduler.last_error)
    assert scheduler.statuses() == ()
