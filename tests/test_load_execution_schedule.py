from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import (
    READINESS_BLOCKED,
    READINESS_READY,
    READINESS_WAITING,
    evaluate_execution_readiness,
)
from custom_components.frakon_energy.load_execution_schedule import (
    ExecutionSchedule,
    ExecutionScheduleConflictError,
    ExecutionScheduleError,
    ExecutionScheduleLedger,
    ExecutionScheduleRepository,
    schedule_storage_key,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)


class _FakeStore:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = data
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


def _snapshot(attempt: ExecutionAttempt | None = None) -> ExecutionActionSnapshot:
    current = attempt or _attempt()
    return ExecutionActionSnapshot.from_attempt_and_intent(
        attempt=current,
        intent=resolve_start_action_intent(_profile()),
        created_at=current.created_at,
    )


def _readiness(*, plan: LoadPlan | None = None, now: datetime, state: str = "off"):
    current_plan = plan or _plan()
    attempt = _attempt(current_plan)
    snapshot = _snapshot(attempt)
    return attempt, snapshot, evaluate_execution_readiness(
        attempt=attempt,
        snapshot=snapshot,
        profile=_profile(),
        plan=current_plan,
        policy=_policy(),
        current_state=state,
        now=now,
    )


def _schedule(*, plan: LoadPlan | None = None, now: datetime = START - timedelta(minutes=2)) -> ExecutionSchedule:
    current_plan = plan or _plan()
    attempt, snapshot, readiness = _readiness(plan=current_plan, now=now)
    return ExecutionSchedule.from_approved_readiness(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=current_plan,
        readiness=readiness,
        created_at=int(now.timestamp()),
    )


def test_waiting_readiness_can_be_durably_scheduled() -> None:
    schedule = _schedule(now=START - timedelta(minutes=2))

    assert schedule.created_from_readiness == READINESS_WAITING
    assert schedule.plan.to_load_plan() == _plan()
    assert schedule.profile_id == "ev-home"
    assert schedule.entity_id == "switch.enyaq_charging"
    assert schedule.service_domain == "switch"
    assert schedule.service_name == "turn_on"
    assert schedule.execution_performed is False
    assert schedule.service_call_performed is False
    assert schedule.executor_available is False


def test_ready_readiness_can_be_scheduled() -> None:
    schedule = _schedule(now=START)

    assert schedule.created_from_readiness == READINESS_READY
    assert schedule.plan.to_load_plan() == _plan()


def test_waiting_and_ready_same_binding_have_same_schedule_identity() -> None:
    waiting = _schedule(now=START - timedelta(minutes=2))
    ready = _schedule(now=START)

    assert waiting.schedule_id == ready.schedule_id
    assert waiting.created_at != ready.created_at
    assert waiting.created_from_readiness != ready.created_from_readiness


def test_blocked_readiness_cannot_be_scheduled() -> None:
    plan = _plan()
    attempt, snapshot, blocked = _readiness(
        plan=plan,
        now=START - timedelta(minutes=2),
        state="on",
    )
    assert blocked.status == READINESS_BLOCKED

    with pytest.raises(ExecutionScheduleError, match="cannot be scheduled"):
        ExecutionSchedule.from_approved_readiness(
            attempt=attempt,
            action_snapshot=snapshot,
            plan=plan,
            readiness=blocked,
            created_at=int((START - timedelta(minutes=2)).timestamp()),
        )


def test_readiness_identity_mismatch_is_rejected() -> None:
    plan = _plan()
    attempt, snapshot, readiness = _readiness(
        plan=plan,
        now=START - timedelta(minutes=2),
    )
    changed = replace(readiness, action_snapshot_id="other")

    with pytest.raises(ExecutionScheduleError, match="action snapshot identity changed"):
        ExecutionSchedule.from_approved_readiness(
            attempt=attempt,
            action_snapshot=snapshot,
            plan=plan,
            readiness=changed,
            created_at=int((START - timedelta(minutes=2)).timestamp()),
        )


def test_persisted_schedule_detects_tampered_identity() -> None:
    raw = _schedule().as_dict()
    raw["schedule_id"] = "0" * 32

    with pytest.raises(ExecutionScheduleError, match="identity does not match"):
        ExecutionSchedule.from_dict(raw)


def test_persisted_schedule_detects_tampered_plan() -> None:
    raw = _schedule().as_dict()
    raw["plan"]["estimated_cost_czk"] = 999.0

    with pytest.raises(ValueError):
        ExecutionSchedule.from_dict(raw)


def test_ledger_exact_retry_is_idempotent_across_waiting_and_ready() -> None:
    ledger = ExecutionScheduleLedger()
    waiting = _schedule(now=START - timedelta(minutes=2))
    ready = _schedule(now=START)

    first = ledger.record(waiting)
    retry = ledger.record(ready)

    assert first.created is True
    assert first.idempotent_replay is False
    assert retry.created is False
    assert retry.idempotent_replay is True
    assert retry.schedule == waiting
    assert len(ledger.schedules) == 1


def test_ledger_rejects_different_plan_for_same_attempt() -> None:
    ledger = ExecutionScheduleLedger()
    first = _schedule(plan=_plan(average=2.0))
    changed = _schedule(plan=_plan(average=2.1))
    assert first.attempt_id == changed.attempt_id
    assert first.schedule_id != changed.schedule_id
    ledger.record(first)

    with pytest.raises(ExecutionScheduleConflictError, match="different immutable schedule"):
        ledger.record(changed)


def test_storage_round_trip_preserves_schedule() -> None:
    ledger = ExecutionScheduleLedger()
    schedule = _schedule()
    ledger.record(schedule)

    restored = ExecutionScheduleLedger.from_storage(ledger.as_storage())

    assert restored.schedules == (schedule,)
    assert restored.get_by_attempt_id(schedule.attempt_id) == schedule
    assert restored.get_by_schedule_id(schedule.schedule_id) == schedule


@pytest.mark.asyncio
async def test_repository_persists_once_and_retry_is_idempotent() -> None:
    store = _FakeStore()
    repository = ExecutionScheduleRepository(store)
    waiting = _schedule(now=START - timedelta(minutes=2))
    ready = _schedule(now=START)

    first = await repository.async_record(waiting)
    retry = await repository.async_record(ready)

    assert first.created is True
    assert retry.idempotent_replay is True
    assert store.saves == 1
    assert len(await repository.async_list()) == 1


@pytest.mark.asyncio
async def test_repository_rolls_back_when_storage_fails() -> None:
    store = _FakeStore()
    store.fail_save = True
    repository = ExecutionScheduleRepository(store)

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await repository.async_record(_schedule())

    assert await repository.async_list() == ()
    assert store.saves == 0


def test_schedule_storage_key_is_stable_and_isolated_per_entry() -> None:
    first = schedule_storage_key("entry-1")
    again = schedule_storage_key("entry-1")
    other = schedule_storage_key("entry-2")

    assert first == again
    assert first != other
    assert first.startswith("frakon_energy.load_execution_schedules.")
