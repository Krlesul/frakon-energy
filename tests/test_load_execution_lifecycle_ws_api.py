from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_lifecycle_ws_api as lifecycle_ws
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import (
    STATE_PREPARED,
    ExecutionLifecycleConflictError,
    ExecutionLifecycleRepository,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import evaluate_execution_readiness
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
        approval_issued_at=int((START - timedelta(minutes=5)).timestamp()),
        approval_expires_at=int((START + timedelta(minutes=5)).timestamp()),
        created_at=int((START - timedelta(minutes=1)).timestamp()),
    ).validated()


def _payload(*, now: datetime = START, plan: LoadPlan | None = None) -> dict[str, Any]:
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
        current_state="off",
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
    repository = ExecutionLifecycleRepository(store)
    monkeypatch.setattr(
        lifecycle_ws,
        "lifecycle_repository",
        lambda hass, entry_id: repository,
    )
    return store, repository


def _mock_readiness(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any], calls: list[int]):
    async def fake_readiness(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(1)
        return payload

    monkeypatch.setattr(
        lifecycle_ws.readiness_ws,
        "async_execution_readiness",
        fake_readiness,
    )


@pytest.mark.asyncio
async def test_prepare_persists_only_prepared_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    store, repository = _repository(monkeypatch)
    calls: list[int] = []
    _mock_readiness(monkeypatch, _payload(), calls)
    hass = SimpleNamespace()

    result = await lifecycle_ws.async_prepare_execution_lifecycle(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        plan_value=_plan().as_dict(),
        now=START,
    )

    assert result["created"] is True
    assert result["idempotent_replay"] is False
    assert result["lifecycle"]["state"] == STATE_PREPARED
    assert result["lifecycle"]["plan"] == _plan().as_dict()
    assert result["prepared_only"] is True
    assert result["execution_performed"] is False
    assert result["service_call_performed"] is False
    assert result["executor_available"] is False
    assert store.saves == 1
    assert len(await repository.async_list()) == 1
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_exact_prepare_retry_returns_existing_without_rechecking_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, repository = _repository(monkeypatch)
    calls: list[int] = []
    _mock_readiness(monkeypatch, _payload(), calls)
    hass = SimpleNamespace()

    first = await lifecycle_ws.async_prepare_execution_lifecycle(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        plan_value=_plan().as_dict(),
        now=START,
    )
    retry = await lifecycle_ws.async_prepare_execution_lifecycle(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        plan_value=_plan().as_dict(),
        now=START + timedelta(minutes=10),
    )

    assert retry["lifecycle"] == first["lifecycle"]
    assert retry["created"] is False
    assert retry["idempotent_replay"] is True
    assert len(calls) == 1
    assert len(await repository.async_list()) == 1


@pytest.mark.asyncio
async def test_same_attempt_with_different_plan_is_conflict_before_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repository(monkeypatch)
    calls: list[int] = []
    _mock_readiness(monkeypatch, _payload(), calls)
    hass = SimpleNamespace()

    await lifecycle_ws.async_prepare_execution_lifecycle(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        plan_value=_plan().as_dict(),
        now=START,
    )
    changed = _plan(average=2.1).as_dict()

    with pytest.raises(ExecutionLifecycleConflictError, match="different plan snapshot"):
        await lifecycle_ws.async_prepare_execution_lifecycle(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            plan_value=changed,
            now=START + timedelta(seconds=1),
        )

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_waiting_readiness_cannot_prepare(monkeypatch: pytest.MonkeyPatch) -> None:
    _, repository = _repository(monkeypatch)
    calls: list[int] = []
    _mock_readiness(
        monkeypatch,
        _payload(now=START - timedelta(seconds=10)),
        calls,
    )

    with pytest.raises(lifecycle_ws.LifecyclePrepareError, match="not ready"):
        await lifecycle_ws.async_prepare_execution_lifecycle(
            SimpleNamespace(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            plan_value=_plan().as_dict(),
            now=START - timedelta(seconds=10),
        )

    assert await repository.async_list() == ()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_prepare_storage_failure_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    store, repository = _repository(monkeypatch, fail_save=True)
    calls: list[int] = []
    _mock_readiness(monkeypatch, _payload(), calls)

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await lifecycle_ws.async_prepare_execution_lifecycle(
            SimpleNamespace(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            plan_value=_plan().as_dict(),
            now=START,
        )

    assert await repository.async_list() == ()
    assert store.saves == 0


@pytest.mark.asyncio
async def test_lifecycle_list_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _, repository = _repository(monkeypatch)
    calls: list[int] = []
    _mock_readiness(monkeypatch, _payload(), calls)
    hass = SimpleNamespace()
    await lifecycle_ws.async_prepare_execution_lifecycle(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        plan_value=_plan().as_dict(),
        now=START,
    )

    result = await lifecycle_ws.async_list_execution_lifecycles(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert len(result["lifecycles"]) == 1
    assert result["lifecycles"][0]["state"] == STATE_PREPARED
    assert result["read_only"] is True
    assert result["execution_performed"] is False
    assert result["executor_available"] is False
