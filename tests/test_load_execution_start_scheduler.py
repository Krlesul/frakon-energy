from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_start_scheduler as scheduler_mod
from custom_components.frakon_energy.load_execution_bounded_dispatch_gate import (
    BOUNDED_GATE_BLOCKED,
    BOUNDED_GATE_READY,
    REASON_STOP_LEASE_REQUIRED,
)
from custom_components.frakon_energy.load_execution_lifecycle import (
    STATE_DISPATCHED,
    STATE_PREPARED,
    STATE_RECOVERY_REQUIRED,
    STATE_VERIFIED,
)
from custom_components.frakon_energy.load_execution_start_dispatcher import (
    StartDispatchUnknownOutcomeError,
)
from custom_components.frakon_energy.load_execution_start_scheduler import (
    STATUS_RECOVERY_REVIEW,
    STATUS_STARTED_VERIFIED,
    STATUS_VERIFIED,
    STATUS_WAITING_STOP_LEASE,
    ExecutionStartScheduler,
)

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)


@dataclass
class _Record:
    state: str = STATE_PREPARED
    attempt_id: str = "attempt-1"
    lifecycle_id: str = "a" * 32
    entity_id: str = "switch.enyaq_charging"
    failure_reason: str | None = None
    dispatch_attempts: int = 0
    service_call_performed: bool | None = False

    def as_dict(self) -> dict[str, Any]:
        return {"service_call_performed": self.service_call_performed}


class _Repo:
    def __init__(self, records: list[_Record]) -> None:
        self.records = records
        self.fail_list = False

    async def async_list(self):
        if self.fail_list:
            raise RuntimeError("start lifecycle store unavailable")
        return tuple(self.records)


class _Hass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}


def _wire_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    repo: _Repo,
    *,
    stop_started: bool = True,
    stop_healthy: bool = True,
    start_recovery: str | None = None,
    stop_recovery: str | None = None,
) -> None:
    monkeypatch.setattr(
        scheduler_mod,
        "lifecycle_repository",
        lambda hass, entry_id: repo,
    )
    monkeypatch.setattr(
        scheduler_mod,
        "lifecycle_recovery_summary",
        lambda hass, entry_id: SimpleNamespace(
            status=start_recovery or scheduler_mod.RECOVERY_OK
        ),
    )
    monkeypatch.setattr(
        scheduler_mod,
        "stop_recovery_summary",
        lambda hass, entry_id: SimpleNamespace(
            status=stop_recovery or scheduler_mod.STOP_RECOVERY_OK
        ),
    )
    monkeypatch.setattr(
        scheduler_mod,
        "stop_scheduler",
        lambda hass, entry_id: SimpleNamespace(
            started=stop_started,
            healthy=stop_healthy,
        ),
    )


def _gate(status: str, reason: str = "test") -> dict[str, Any]:
    return {
        "bounded_dispatch_gate": {
            "status": status,
            "reason": reason,
        }
    }


@pytest.mark.asyncio
async def test_prepared_without_stop_lease_waits_without_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _Record()
    repo = _Repo([record])
    _wire_dependencies(monkeypatch, repo)
    dispatch_calls: list[str] = []

    async def bounded_gate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return _gate(BOUNDED_GATE_BLOCKED, REASON_STOP_LEASE_REQUIRED)

    async def dispatch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        dispatch_calls.append("dispatch")
        return {}

    monkeypatch.setattr(scheduler_mod, "async_bounded_dispatch_gate", bounded_gate)
    monkeypatch.setattr(scheduler_mod, "async_dispatch_bounded_start", dispatch)

    scheduler = ExecutionStartScheduler(_Hass(), "entry-1")  # type: ignore[arg-type]
    scheduler._started = True
    await scheduler.async_refresh(now=NOW)

    assert dispatch_calls == []
    assert scheduler.healthy is True
    assert scheduler.statuses()[0].status == STATUS_WAITING_STOP_LEASE
    assert scheduler.statuses()[0].can_redispatch is False


@pytest.mark.asyncio
async def test_ready_prepared_start_delegates_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _Record()
    repo = _Repo([record])
    _wire_dependencies(monkeypatch, repo)
    calls: list[dict[str, Any]] = []

    async def bounded_gate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return _gate(BOUNDED_GATE_READY)

    async def dispatch(hass, *, entry_id, attempt_id, context, now):
        calls.append(
            {
                "entry_id": entry_id,
                "attempt_id": attempt_id,
                "context": context,
                "now": now,
            }
        )
        record.state = STATE_VERIFIED
        record.dispatch_attempts = 1
        record.service_call_performed = True
        return {
            "status": "start_verified",
            "physical_dispatch_attempted": True,
            "service_call_performed": True,
            "execution_performed": True,
        }

    monkeypatch.setattr(scheduler_mod, "async_bounded_dispatch_gate", bounded_gate)
    monkeypatch.setattr(scheduler_mod, "async_dispatch_bounded_start", dispatch)

    scheduler = ExecutionStartScheduler(_Hass(), "entry-1")  # type: ignore[arg-type]
    scheduler._started = True
    await scheduler.async_refresh(now=NOW)

    assert calls == [
        {
            "entry_id": "entry-1",
            "attempt_id": "attempt-1",
            "context": None,
            "now": NOW,
        }
    ]
    status = scheduler.statuses()[0]
    assert status.status == STATUS_STARTED_VERIFIED
    assert status.physical_dispatch_attempted is True
    assert status.service_call_performed is True
    assert status.execution_performed is True
    assert status.can_redispatch is False


@pytest.mark.asyncio
async def test_unknown_start_outcome_is_never_redispatched_on_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _Record()
    repo = _Repo([record])
    _wire_dependencies(monkeypatch, repo)
    dispatch_calls = 0
    verify_calls = 0

    async def bounded_gate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return _gate(BOUNDED_GATE_READY)

    async def dispatch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal dispatch_calls
        dispatch_calls += 1
        record.state = STATE_RECOVERY_REQUIRED
        record.dispatch_attempts = 1
        record.service_call_performed = None
        raise StartDispatchUnknownOutcomeError("unknown start outcome")

    async def verify(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal verify_calls
        verify_calls += 1
        raise ValueError("operator review required")

    monkeypatch.setattr(scheduler_mod, "async_bounded_dispatch_gate", bounded_gate)
    monkeypatch.setattr(scheduler_mod, "async_dispatch_bounded_start", dispatch)
    monkeypatch.setattr(scheduler_mod, "async_verify_recovery_lifecycle", verify)

    scheduler = ExecutionStartScheduler(_Hass(), "entry-1")  # type: ignore[arg-type]
    scheduler._started = True
    await scheduler.async_refresh(now=NOW)
    assert scheduler.statuses()[0].status == STATUS_RECOVERY_REVIEW

    await scheduler.async_refresh(now=NOW + timedelta(seconds=1))

    assert dispatch_calls == 1
    assert verify_calls == 1
    assert scheduler.statuses()[0].status == STATUS_RECOVERY_REVIEW
    assert scheduler.statuses()[0].can_redispatch is False


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [STATE_RECOVERY_REQUIRED, STATE_DISPATCHED])
async def test_existing_start_dispatch_only_verifies_without_redispatch(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    record = _Record(
        state=state,
        dispatch_attempts=1,
        service_call_performed=None if state == STATE_RECOVERY_REQUIRED else True,
    )
    repo = _Repo([record])
    _wire_dependencies(monkeypatch, repo)
    dispatch_calls = 0
    verify_calls = 0

    async def dispatch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal dispatch_calls
        dispatch_calls += 1
        return {}

    async def verify(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal verify_calls
        verify_calls += 1
        record.state = STATE_VERIFIED
        return {"service_call_performed": record.service_call_performed}

    monkeypatch.setattr(scheduler_mod, "async_dispatch_bounded_start", dispatch)
    monkeypatch.setattr(scheduler_mod, "async_verify_recovery_lifecycle", verify)

    scheduler = ExecutionStartScheduler(_Hass(), "entry-1")  # type: ignore[arg-type]
    scheduler._started = True
    await scheduler.async_refresh(now=NOW)

    assert dispatch_calls == 0
    assert verify_calls == 1
    assert scheduler.statuses()[0].status == STATUS_VERIFIED
    assert scheduler.statuses()[0].can_redispatch is False


@pytest.mark.asyncio
async def test_unhealthy_autonomous_stop_dependency_blocks_all_start_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _Repo([_Record()])
    _wire_dependencies(monkeypatch, repo, stop_healthy=False)
    gate_calls = 0
    dispatch_calls = 0

    async def bounded_gate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal gate_calls
        gate_calls += 1
        return _gate(BOUNDED_GATE_READY)

    async def dispatch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal dispatch_calls
        dispatch_calls += 1
        return {}

    monkeypatch.setattr(scheduler_mod, "async_bounded_dispatch_gate", bounded_gate)
    monkeypatch.setattr(scheduler_mod, "async_dispatch_bounded_start", dispatch)

    scheduler = ExecutionStartScheduler(_Hass(), "entry-1")  # type: ignore[arg-type]
    scheduler._started = True
    await scheduler.async_refresh(now=NOW)

    assert scheduler.healthy is False
    assert scheduler.last_error == "autonomous_stop_runtime_not_ready"
    assert gate_calls == 0
    assert dispatch_calls == 0


@pytest.mark.asyncio
async def test_startup_scan_processes_prepared_bounded_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _Record()
    repo = _Repo([record])
    _wire_dependencies(monkeypatch, repo)
    dispatch_calls = 0

    async def bounded_gate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return _gate(BOUNDED_GATE_READY)

    async def dispatch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal dispatch_calls
        dispatch_calls += 1
        record.state = STATE_VERIFIED
        record.service_call_performed = True
        return {
            "status": "start_verified",
            "physical_dispatch_attempted": True,
            "service_call_performed": True,
            "execution_performed": True,
        }

    monkeypatch.setattr(scheduler_mod, "async_bounded_dispatch_gate", bounded_gate)
    monkeypatch.setattr(scheduler_mod, "async_dispatch_bounded_start", dispatch)

    scheduler = ExecutionStartScheduler(_Hass(), "entry-1")  # type: ignore[arg-type]
    await scheduler.async_start()

    assert scheduler.started is True
    assert scheduler.healthy is True
    assert dispatch_calls == 1
    assert scheduler.statuses()[0].status == STATUS_STARTED_VERIFIED


@pytest.mark.asyncio
async def test_startup_store_failure_is_fail_closed_not_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _Repo([_Record()])
    repo.fail_list = True
    _wire_dependencies(monkeypatch, repo)

    scheduler = ExecutionStartScheduler(_Hass(), "entry-1")  # type: ignore[arg-type]
    await scheduler.async_start()

    assert scheduler.started is True
    assert scheduler.healthy is False
    assert "start lifecycle store unavailable" in str(scheduler.last_error)


@pytest.mark.asyncio
async def test_stopped_scheduler_does_not_process_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _Repo([_Record()])
    _wire_dependencies(monkeypatch, repo)
    dispatch_calls = 0

    async def dispatch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal dispatch_calls
        dispatch_calls += 1
        return {}

    monkeypatch.setattr(scheduler_mod, "async_dispatch_bounded_start", dispatch)

    scheduler = ExecutionStartScheduler(_Hass(), "entry-1")  # type: ignore[arg-type]
    await scheduler.async_stop()
    await scheduler.async_refresh(now=NOW)

    assert dispatch_calls == 0
    assert scheduler.started is False
