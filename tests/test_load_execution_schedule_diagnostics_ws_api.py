from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_schedule_diagnostics_ws_api as diagnostics_ws
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
    include_schedule: bool = True,
    include_lifecycle: bool = False,
) -> tuple[ExecutionScheduleRepository, ExecutionLifecycleRepository]:
    schedules = ExecutionScheduleRepository(_FakeStore())
    lifecycles = ExecutionLifecycleRepository(_FakeStore())
    if include_schedule:
        await schedules.async_record(_schedule())
    if include_lifecycle:
        await lifecycles.async_prepare(_lifecycle())
    monkeypatch.setattr(diagnostics_ws, "schedule_repository", lambda hass, entry_id: schedules)
    monkeypatch.setattr(diagnostics_ws, "lifecycle_repository", lambda hass, entry_id: lifecycles)
    return schedules, lifecycles


def _recovery(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    monkeypatch.setattr(
        diagnostics_ws,
        "lifecycle_recovery_summary",
        lambda hass, entry_id: LifecycleRecoverySummary(
            entry_id=entry_id,
            status=status,
            scanned=0,
            transitioned_to_recovery=0,
            recovery_required=0,
            dispatched_pending_verification=0,
            error="storage unavailable" if status == RECOVERY_FAILED else None,
        ),
    )


@pytest.mark.asyncio
async def test_diagnostics_returns_prepare_candidate_at_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _install_repositories(monkeypatch)
    _recovery(monkeypatch, RECOVERY_OK)

    result = await diagnostics_ws.async_execution_schedule_diagnostics(
        SimpleNamespace(),  # type: ignore[arg-type]
        entry_id="entry-1",
        now=START,
    )

    assert len(result["diagnostics"]) == 1
    assert result["diagnostics"][0]["status"] == "prepare_now"
    assert result["diagnostics"][0]["scheduler_should_prepare"] is True
    assert result["scheduler_prepare_candidates"] == ["attempt-1"]
    assert result["read_only"] is True
    assert result["state_transition_performed"] is False
    assert result["execution_performed"] is False
    assert result["service_call_performed"] is False
    assert result["executor_available"] is False


@pytest.mark.asyncio
async def test_failed_startup_recovery_removes_prepare_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _install_repositories(monkeypatch)
    _recovery(monkeypatch, RECOVERY_FAILED)

    result = await diagnostics_ws.async_execution_schedule_diagnostics(
        SimpleNamespace(),  # type: ignore[arg-type]
        entry_id="entry-1",
        now=START,
    )

    assert result["diagnostics"][0]["status"] == "prepare_now"
    assert result["diagnostics"][0]["next_action"] == "blocked_by_startup_recovery"
    assert result["diagnostics"][0]["scheduler_should_prepare"] is False
    assert result["scheduler_prepare_candidates"] == []


@pytest.mark.asyncio
async def test_existing_lifecycle_suppresses_prepare_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _install_repositories(monkeypatch, include_lifecycle=True)
    _recovery(monkeypatch, RECOVERY_OK)

    result = await diagnostics_ws.async_execution_schedule_diagnostics(
        SimpleNamespace(),  # type: ignore[arg-type]
        entry_id="entry-1",
        now=START + timedelta(seconds=5),
    )

    assert result["diagnostics"][0]["status"] == "lifecycle_exists"
    assert result["diagnostics"][0]["lifecycle_state"] == "prepared"
    assert result["scheduler_prepare_candidates"] == []


@pytest.mark.asyncio
async def test_attempt_filter_returns_only_requested_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _install_repositories(monkeypatch)
    _recovery(monkeypatch, RECOVERY_OK)

    result = await diagnostics_ws.async_execution_schedule_diagnostics(
        SimpleNamespace(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START - timedelta(seconds=10),
    )

    assert len(result["diagnostics"]) == 1
    assert result["diagnostics"][0]["attempt_id"] == "attempt-1"


@pytest.mark.asyncio
async def test_missing_attempt_filter_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    await _install_repositories(monkeypatch)
    _recovery(monkeypatch, RECOVERY_OK)

    with pytest.raises(ValueError, match="schedule not found"):
        await diagnostics_ws.async_execution_schedule_diagnostics(
            SimpleNamespace(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="missing",
            now=START,
        )
