from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_prepare_scheduled_ws_api as bridge
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle_ws_api import LifecyclePrepareError
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


def _schedule() -> ExecutionSchedule:
    attempt = _attempt()
    snapshot = ExecutionActionSnapshot.from_attempt_and_intent(
        attempt=attempt,
        intent=resolve_start_action_intent(_profile()),
        created_at=attempt.created_at,
    )
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


async def _repository(monkeypatch: pytest.MonkeyPatch, *, include_schedule: bool = True):
    repository = ExecutionScheduleRepository(_FakeStore())
    schedule = _schedule()
    if include_schedule:
        await repository.async_record(schedule)
    monkeypatch.setattr(bridge, "schedule_repository", lambda hass, entry_id: repository)
    return repository, schedule


@pytest.mark.asyncio
async def test_prepare_scheduled_passes_exact_persisted_plan_to_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, schedule = await _repository(monkeypatch)
    calls: list[dict[str, Any]] = []

    async def fake_prepare(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "lifecycle": {"state": "prepared"},
            "created": True,
            "idempotent_replay": False,
            "prepared_only": True,
            "execution_performed": False,
            "service_call_performed": False,
            "executor_available": False,
        }

    monkeypatch.setattr(bridge, "async_prepare_execution_lifecycle", fake_prepare)

    result = await bridge.async_prepare_scheduled_execution(
        SimpleNamespace(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START,
    )

    assert len(calls) == 1
    assert calls[0]["entry_id"] == "entry-1"
    assert calls[0]["attempt_id"] == "attempt-1"
    assert calls[0]["plan_value"] == schedule.plan.as_dict()
    assert calls[0]["now"] == START
    assert result["schedule"] == schedule.as_dict()
    assert result["persisted_plan_used"] is True
    assert result["execution_performed"] is False
    assert result["service_call_performed"] is False
    assert result["executor_available"] is False


@pytest.mark.asyncio
async def test_prepare_scheduled_has_no_client_plan_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    _, schedule = await _repository(monkeypatch)
    captured: dict[str, Any] = {}

    async def fake_prepare(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "lifecycle": {"state": "prepared"},
            "execution_performed": False,
            "service_call_performed": False,
            "executor_available": False,
        }

    monkeypatch.setattr(bridge, "async_prepare_execution_lifecycle", fake_prepare)
    await bridge.async_prepare_scheduled_execution(
        SimpleNamespace(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START,
    )

    assert captured["plan_value"] == schedule.plan.as_dict()


@pytest.mark.asyncio
async def test_missing_schedule_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    await _repository(monkeypatch, include_schedule=False)

    with pytest.raises(bridge.PrepareScheduledError, match="schedule not found"):
        await bridge.async_prepare_scheduled_execution(
            SimpleNamespace(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START,
        )


@pytest.mark.asyncio
async def test_waiting_lifecycle_prepare_rejection_propagates_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _ = await _repository(monkeypatch)

    async def fake_prepare(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise LifecyclePrepareError("execution is not ready for preparation: waiting/plan_start_in_future")

    monkeypatch.setattr(bridge, "async_prepare_execution_lifecycle", fake_prepare)

    with pytest.raises(LifecyclePrepareError, match="not ready"):
        await bridge.async_prepare_scheduled_execution(
            SimpleNamespace(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START - timedelta(minutes=1),
        )


@pytest.mark.asyncio
async def test_existing_lifecycle_replay_evidence_is_forwarded_conservatively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _ = await _repository(monkeypatch)

    async def fake_prepare(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "lifecycle": {"state": "verified", "service_call_status": "unknown"},
            "created": False,
            "idempotent_replay": True,
            "prepared_only": False,
            "execution_performed": False,
            "service_call_performed": None,
            "executor_available": False,
        }

    monkeypatch.setattr(bridge, "async_prepare_execution_lifecycle", fake_prepare)
    result = await bridge.async_prepare_scheduled_execution(
        SimpleNamespace(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START + timedelta(minutes=10),
    )

    assert result["lifecycle"]["idempotent_replay"] is True
    assert result["service_call_performed"] is None
    assert result["execution_performed"] is False
    assert result["executor_available"] is False


def test_naive_now_is_rejected() -> None:
    with pytest.raises(bridge.PrepareScheduledError, match="timezone-aware"):
        # Validation happens before repository access.
        import asyncio

        asyncio.run(
            bridge.async_prepare_scheduled_execution(
                SimpleNamespace(),  # type: ignore[arg-type]
                entry_id="entry-1",
                attempt_id="attempt-1",
                now=datetime(2026, 8, 8, 1, 0),
            )
        )
