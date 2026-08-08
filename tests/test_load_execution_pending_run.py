from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import ExecutionPlanSnapshot
from custom_components.frakon_energy.load_execution_pending_run import (
    ExecutionPendingRun,
    ExecutionPendingRunRepository,
    PendingRunConflictError,
    PendingRunError,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_GENERIC, LoadProfile

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


class _Store:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.saves = 0
        self.fail = False

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        self.saves += 1
        if self.fail:
            raise RuntimeError("pending run store unavailable")
        self.data = data


def _profile() -> LoadProfile:
    return LoadProfile(
        "test-load",
        "Test load",
        PROFILE_KIND_GENERIC,
        60,
        2.0,
        entity_id="switch.test_load",
    )


def _attempt() -> ExecutionAttempt:
    return ExecutionAttempt(
        attempt_id="attempt-1",
        entry_id="entry-1",
        profile_id="test-load",
        entity_id="switch.test_load",
        approval_id="approval-1",
        approval_fingerprint="a" * 64,
        snapshot_digest="b" * 64,
        intent="execute_load_plan",
        approval_issued_at=100,
        approval_expires_at=300,
        created_at=150,
    ).validated()


def _snapshot() -> ExecutionActionSnapshot:
    attempt = _attempt()
    return ExecutionActionSnapshot.from_attempt_and_intent(
        attempt=attempt,
        intent=resolve_start_action_intent(_profile()),
        created_at=attempt.created_at,
    )


def _plan(*, starts_at: datetime = NOW + timedelta(hours=1)) -> ExecutionPlanSnapshot:
    duration = 60
    power = 2.0
    energy = power * duration / 60
    plan = LoadPlan(
        load_id="test-load",
        name="Test load",
        starts_at=starts_at.isoformat(),
        ends_at=(starts_at + timedelta(minutes=duration)).isoformat(),
        duration_minutes=duration,
        interval_count=duration // 15,
        power_kw=power,
        average_czk_kwh=2.0,
        minimum_czk_kwh=1.0,
        maximum_czk_kwh=3.0,
        estimated_energy_kwh=energy,
        estimated_cost_czk=energy * 2.0,
    )
    return ExecutionPlanSnapshot.from_load_plan(plan)


def _pending(*, plan: ExecutionPlanSnapshot | None = None) -> ExecutionPendingRun:
    return ExecutionPendingRun.from_records(
        attempt=_attempt(),
        action_snapshot=_snapshot(),
        plan=plan or _plan(),
        created_at=160,
    )


def test_pending_run_binds_exact_attempt_action_and_plan() -> None:
    pending = _pending()

    assert pending.entry_id == "entry-1"
    assert pending.attempt_id == "attempt-1"
    assert pending.action_snapshot_id == _snapshot().snapshot_id
    assert pending.entity_id == "switch.test_load"
    assert pending.service_domain == "switch"
    assert pending.service_name == "turn_on"
    assert pending.desired_state == "on"
    assert pending.plan_digest == pending.plan.digest()
    assert pending.service_call_performed is False
    assert pending.executor_available is False
    assert ExecutionPendingRun.from_dict(pending.as_dict()) == pending


@pytest.mark.asyncio
async def test_repository_exact_retry_is_idempotent_and_changed_plan_conflicts() -> None:
    store = _Store()
    repo = ExecutionPendingRunRepository(store)
    first = _pending()

    created = await repo.async_record(first)
    replay = await repo.async_record(first)

    assert created.created is True
    assert replay.created is False
    assert replay.idempotent_replay is True
    assert replay.pending_run == first
    assert store.saves == 1

    changed = _pending(plan=_plan(starts_at=NOW + timedelta(hours=2)))
    with pytest.raises(PendingRunConflictError, match="different immutable pending run"):
        await repo.async_record(changed)
    assert store.saves == 1


@pytest.mark.asyncio
async def test_store_failure_rolls_back_in_memory_ledger() -> None:
    store = _Store()
    repo = ExecutionPendingRunRepository(store)
    store.fail = True

    with pytest.raises(RuntimeError, match="pending run store unavailable"):
        await repo.async_record(_pending())

    assert await repo.async_list() == ()
    assert store.saves == 1


def test_tampered_persisted_start_action_is_rejected_fail_closed() -> None:
    value = _pending().as_dict()
    value["service_name"] = "toggle"

    with pytest.raises(PendingRunError, match="not allowlisted"):
        ExecutionPendingRun.from_dict(value)


def test_tampered_plan_digest_is_rejected() -> None:
    value = _pending().as_dict()
    value["plan_digest"] = "c" * 64

    with pytest.raises(PendingRunError, match="plan digest"):
        ExecutionPendingRun.from_dict(value)
