from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_schedule_ws_api as schedule_ws
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle_recovery import LifecycleRecoveryBlockedError
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import evaluate_execution_readiness
from custom_components.frakon_energy.load_execution_schedule import (
    ExecutionScheduleConflictError,
    ExecutionScheduleRepository,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)


class _FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.saves = 0
        self.fail_save = False

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        if self.fail_save:
            raise RuntimeError("storage unavailable")
        self.data = data
        self.saves += 1


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


def _plan(*, average: float = 2.0) -> LoadPlan:
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
        average_czk_kwh=average,
        minimum_czk_kwh=1.5,
        maximum_czk_kwh=2.5,
        estimated_energy_kwh=energy,
        estimated_cost_czk=energy * average,
    )


def _attempt(plan: LoadPlan | None = None) -> ExecutionAttempt:
    current_plan = plan or _plan()
    return ExecutionAttempt(
        attempt_id="attempt-1",
        entry_id="entry-1",
        profile_id="ev-home",
        entity_id="switch.enyaq_charging",
        approval_id="approval-1",
        approval_fingerprint="a" * 64,
        snapshot_digest=execution_snapshot_digest(_profile(), current_plan, _policy()),
        intent="execute_load_plan",
        approval_issued_at=int((START - timedelta(minutes=10)).timestamp()),
        approval_expires_at=int((START + timedelta(minutes=5)).timestamp()),
        created_at=int((START - timedelta(minutes=5)).timestamp()),
    ).validated()


def _readiness_payload(*, now: datetime, plan: LoadPlan | None = None, state: str = "off") -> dict[str, Any]:
    current_plan = plan or _plan()
    attempt = _attempt(current_plan)
    snapshot = ExecutionActionSnapshot.from_attempt_and_intent(
        attempt=attempt,
        intent=resolve_start_action_intent(_profile()),
        created_at=attempt.created_at,
    )
    readiness = evaluate_execution_readiness(
        attempt=attempt,
        snapshot=snapshot,
        profile=_profile(),
        plan=current_plan,
        policy=_policy(),
        current_state=state,
        now=now,
    )
    return {
        "entry_id": "entry-1",
        "attempt": attempt.as_dict(),
        "action_snapshot": snapshot.as_dict(),
        "profile": _profile().as_dict(),
        "policy": _policy().as_dict(),
        "plan": current_plan.as_dict(),
        "readiness": readiness.as_dict(),
        "read_only": True,
        "execution_performed": False,
        "service_call_performed": False,
        "executor_available": False,
    }


def _repository(monkeypatch: pytest.MonkeyPatch, *, fail_save: bool = False):
    store = _FakeStore()
    store.fail_save = fail_save
    repository = ExecutionScheduleRepository(store)
    monkeypatch.setattr(schedule_ws, "schedule_repository", lambda hass, entry_id: repository)
    monkeypatch.setattr(schedule_ws, "assert_lifecycle_recovery_ready", lambda hass, entry_id: None)
    return store, repository


def _mock_readiness(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
    calls: list[dict[str, Any]],
) -> None:
    async def fake_readiness(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return payload

    monkeypatch.setattr(schedule_ws.readiness_ws, "async_execution_readiness", fake_readiness)


@pytest.mark.asyncio
async def test_waiting_plan_is_persisted_before_start(monkeypatch: pytest.MonkeyPatch) -> None:
    store, repository = _repository(monkeypatch)
    calls: list[dict[str, Any]] = []
    _mock_readiness(monkeypatch, _readiness_payload(now=START - timedelta(minutes=2)), calls)

    result = await schedule_ws.async_create_execution_schedule(
        SimpleNamespace(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        plan_value=_plan().as_dict(),
        now=START - timedelta(minutes=2),
    )

    assert result["created"] is True
    assert result["schedule"]["created_from_readiness"] == "waiting"
    assert result["schedule"]["plan"] == _plan().as_dict()
    assert result["execution_performed"] is False
    assert result["service_call_performed"] is False
    assert result["executor_available"] is False
    assert store.saves == 1
    assert len(await repository.async_list()) == 1
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_exact_schedule_retry_does_not_rerun_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    store, repository = _repository(monkeypatch)
    calls: list[dict[str, Any]] = []
    _mock_readiness(monkeypatch, _readiness_payload(now=START - timedelta(minutes=2)), calls)
    hass = SimpleNamespace()

    first = await schedule_ws.async_create_execution_schedule(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        plan_value=_plan().as_dict(),
        now=START - timedelta(minutes=2),
    )
    retry = await schedule_ws.async_create_execution_schedule(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        plan_value=_plan().as_dict(),
        now=START,
    )

    assert retry["schedule"] == first["schedule"]
    assert retry["created"] is False
    assert retry["idempotent_replay"] is True
    assert store.saves == 1
    assert len(calls) == 1
    assert len(await repository.async_list()) == 1


@pytest.mark.asyncio
async def test_same_attempt_different_plan_is_conflict_before_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    _repository(monkeypatch)
    calls: list[dict[str, Any]] = []
    _mock_readiness(monkeypatch, _readiness_payload(now=START - timedelta(minutes=2)), calls)
    hass = SimpleNamespace()
    await schedule_ws.async_create_execution_schedule(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        plan_value=_plan().as_dict(),
        now=START - timedelta(minutes=2),
    )

    with pytest.raises(ExecutionScheduleConflictError, match="different plan snapshot"):
        await schedule_ws.async_create_execution_schedule(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            plan_value=_plan(average=2.1).as_dict(),
            now=START - timedelta(minutes=1),
        )

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_blocked_readiness_does_not_create_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    _, repository = _repository(monkeypatch)
    calls: list[dict[str, Any]] = []
    _mock_readiness(
        monkeypatch,
        _readiness_payload(now=START - timedelta(minutes=2), state="on"),
        calls,
    )

    with pytest.raises(ValueError, match="cannot be scheduled"):
        await schedule_ws.async_create_execution_schedule(
            SimpleNamespace(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            plan_value=_plan().as_dict(),
            now=START - timedelta(minutes=2),
        )

    assert await repository.async_list() == ()


@pytest.mark.asyncio
async def test_schedule_readiness_uses_only_persisted_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    _, _ = _repository(monkeypatch)
    create_calls: list[dict[str, Any]] = []
    _mock_readiness(monkeypatch, _readiness_payload(now=START - timedelta(minutes=2)), create_calls)
    hass = SimpleNamespace()
    await schedule_ws.async_create_execution_schedule(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        plan_value=_plan().as_dict(),
        now=START - timedelta(minutes=2),
    )

    readiness_calls: list[dict[str, Any]] = []
    _mock_readiness(monkeypatch, _readiness_payload(now=START), readiness_calls)
    result = await schedule_ws.async_execution_schedule_readiness(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START,
    )

    assert len(readiness_calls) == 1
    assert readiness_calls[0]["plan_value"] == _plan().as_dict()
    assert result["schedule"]["plan"] == _plan().as_dict()
    assert result["persisted_plan_used"] is True
    assert result["read_only"] is True
    assert result["execution_performed"] is False
    assert result["service_call_performed"] is False
    assert result["executor_available"] is False


@pytest.mark.asyncio
async def test_schedule_creation_is_blocked_when_startup_recovery_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repository(monkeypatch)
    monkeypatch.setattr(
        schedule_ws,
        "assert_lifecycle_recovery_ready",
        lambda hass, entry_id: (_ for _ in ()).throw(
            LifecycleRecoveryBlockedError("execution lifecycle recovery is failed")
        ),
    )

    with pytest.raises(LifecycleRecoveryBlockedError, match="recovery is failed"):
        await schedule_ws.async_create_execution_schedule(
            SimpleNamespace(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            plan_value=_plan().as_dict(),
            now=START - timedelta(minutes=2),
        )


@pytest.mark.asyncio
async def test_schedule_storage_failure_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    store, repository = _repository(monkeypatch, fail_save=True)
    calls: list[dict[str, Any]] = []
    _mock_readiness(monkeypatch, _readiness_payload(now=START - timedelta(minutes=2)), calls)

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await schedule_ws.async_create_execution_schedule(
            SimpleNamespace(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            plan_value=_plan().as_dict(),
            now=START - timedelta(minutes=2),
        )

    assert await repository.async_list() == ()
    assert store.saves == 0


@pytest.mark.asyncio
async def test_schedule_list_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _repository(monkeypatch)
    calls: list[dict[str, Any]] = []
    _mock_readiness(monkeypatch, _readiness_payload(now=START - timedelta(minutes=2)), calls)
    hass = SimpleNamespace()
    await schedule_ws.async_create_execution_schedule(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        plan_value=_plan().as_dict(),
        now=START - timedelta(minutes=2),
    )

    result = await schedule_ws.async_list_execution_schedules(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert len(result["schedules"]) == 1
    assert result["read_only"] is True
    assert result["execution_performed"] is False
    assert result["service_call_performed"] is False
    assert result["executor_available"] is False
