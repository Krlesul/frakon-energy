from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import ExecutionPlanSnapshot
from custom_components.frakon_energy.load_execution_pending_run import ExecutionPendingRun
from custom_components.frakon_energy.load_execution_pending_run_cancellation import (
    PendingRunCancellation,
    PendingRunCancellationConflictError,
    PendingRunCancellationRepository,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_GENERIC, LoadProfile

NOW = datetime(2026, 8, 8, 15, 0, tzinfo=timezone.utc)


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
            raise RuntimeError("cancellation store unavailable")
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


def _pending(*, starts_at: datetime | None = None) -> ExecutionPendingRun:
    start = starts_at or NOW + timedelta(hours=1)
    profile = _profile()
    plan = LoadPlan(
        load_id=profile.profile_id,
        name=profile.name,
        starts_at=start.isoformat(),
        ends_at=(start + timedelta(hours=1)).isoformat(),
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


def test_cancellation_binds_exact_pending_scope_and_round_trips() -> None:
    pending = _pending()
    cancellation = PendingRunCancellation.from_pending_run(
        pending,
        cancelled_at=int(NOW.timestamp()),
        cancelled_by="admin-user-id",
    )

    assert cancellation.attempt_id == pending.attempt_id
    assert cancellation.pending_run_id == pending.pending_run_id
    assert cancellation.plan_digest == pending.plan_digest
    assert cancellation.entity_id == pending.entity_id
    assert cancellation.cancelled_by == "admin-user-id"
    assert cancellation.service_call_performed is False
    assert cancellation.execution_performed is False
    assert PendingRunCancellation.from_dict(cancellation.as_dict()) == cancellation


@pytest.mark.asyncio
async def test_cancellation_repository_exact_retry_is_idempotent() -> None:
    store = _Store()
    repo = PendingRunCancellationRepository(store)
    pending = _pending()
    first = PendingRunCancellation.from_pending_run(
        pending,
        cancelled_at=int(NOW.timestamp()),
        cancelled_by="admin-user-id",
    )
    later_retry = PendingRunCancellation.from_pending_run(
        pending,
        cancelled_at=int((NOW + timedelta(minutes=1)).timestamp()),
        cancelled_by="another-admin-id",
    )

    created = await repo.async_record(first)
    replay = await repo.async_record(later_retry)

    assert created.created is True
    assert replay.created is False
    assert replay.idempotent_replay is True
    assert replay.cancellation == first
    assert store.saves == 1

    changed_pending = _pending(starts_at=NOW + timedelta(hours=2))
    changed = PendingRunCancellation.from_pending_run(
        changed_pending,
        cancelled_at=int(NOW.timestamp()),
        cancelled_by="admin-user-id",
    )
    with pytest.raises(PendingRunCancellationConflictError):
        await repo.async_record(changed)
    assert store.saves == 1


@pytest.mark.asyncio
async def test_cancellation_store_failure_rolls_back() -> None:
    store = _Store()
    repo = PendingRunCancellationRepository(store)
    store.fail = True
    cancellation = PendingRunCancellation.from_pending_run(
        _pending(),
        cancelled_at=int(NOW.timestamp()),
        cancelled_by=None,
    )

    with pytest.raises(RuntimeError, match="cancellation store unavailable"):
        await repo.async_record(cancellation)

    assert await repo.async_list() == ()
    assert store.saves == 1
