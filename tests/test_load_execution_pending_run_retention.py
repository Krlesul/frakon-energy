from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_pending_run_retention as retention
from custom_components.frakon_energy import load_execution_pending_run_retention_runtime as retention_runtime
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import (
    STATE_PREPARED,
    STATE_RECOVERY_REQUIRED,
    STATE_VERIFIED,
    ExecutionPlanSnapshot,
)
from custom_components.frakon_energy.load_execution_pending_run import (
    ExecutionPendingRun,
    ExecutionPendingRunRepository,
)
from custom_components.frakon_energy.load_execution_pending_run_retention import (
    PENDING_RUN_RETENTION_DAYS,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_GENERIC, LoadProfile

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


class _Store:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.fail = False
        self.saves = 0

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        self.saves += 1
        if self.fail:
            raise RuntimeError("pending retention store unavailable")
        self.data = data


class _LifecycleRepo:
    def __init__(self, states: dict[str, str | None]) -> None:
        self.states = states

    async def async_get_by_attempt_id(self, attempt_id: str):
        state = self.states.get(attempt_id)
        return None if state is None else SimpleNamespace(state=state)


class _Hass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}


def _pending(attempt_id: str, *, ends_at: datetime) -> ExecutionPendingRun:
    starts_at = ends_at - timedelta(hours=1)
    profile = LoadProfile(
        "retention-load",
        "Retention load",
        PROFILE_KIND_GENERIC,
        60,
        0.1,
        entity_id="input_boolean.retention_load",
    )
    attempt = ExecutionAttempt(
        attempt_id=attempt_id,
        entry_id="entry-1",
        profile_id=profile.profile_id,
        entity_id=profile.entity_id,
        approval_id=f"approval-{attempt_id}",
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
    plan = LoadPlan(
        load_id=profile.profile_id,
        name=profile.name,
        starts_at=starts_at.isoformat(),
        ends_at=ends_at.isoformat(),
        duration_minutes=60,
        interval_count=4,
        power_kw=0.1,
        average_czk_kwh=2.0,
        minimum_czk_kwh=1.0,
        maximum_czk_kwh=3.0,
        estimated_energy_kwh=0.1,
        estimated_cost_czk=0.2,
    )
    return ExecutionPendingRun.from_records(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=ExecutionPlanSnapshot.from_load_plan(plan),
        created_at=160,
    )


@pytest.mark.asyncio
async def test_retention_prunes_only_old_redundant_or_terminal_pending_copies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_end = NOW - timedelta(days=PENDING_RUN_RETENTION_DAYS + 1)
    young_end = NOW - timedelta(days=1)
    records = [
        _pending("old-no-lifecycle", ends_at=old_end),
        _pending("old-verified", ends_at=old_end),
        _pending("old-prepared", ends_at=old_end),
        _pending("old-recovery", ends_at=old_end),
        _pending("young-no-lifecycle", ends_at=young_end),
    ]
    repo = ExecutionPendingRunRepository(_Store())
    for record in records:
        await repo.async_record(record)
    lifecycles = _LifecycleRepo(
        {
            "old-no-lifecycle": None,
            "old-verified": STATE_VERIFIED,
            "old-prepared": STATE_PREPARED,
            "old-recovery": STATE_RECOVERY_REQUIRED,
            "young-no-lifecycle": None,
        }
    )
    monkeypatch.setattr(
        retention,
        "pending_run_repository",
        lambda hass, entry_id: repo,
    )
    monkeypatch.setattr(
        retention,
        "lifecycle_repository",
        lambda hass, entry_id: lifecycles,
    )

    result = await retention.async_prune_pending_run_audit(
        _Hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        now=NOW,
    )

    assert result.scanned == 5
    assert result.eligible == 2
    assert result.pruned == 2
    assert result.retained_active == 2
    assert result.retained_young == 1
    assert result.retention_days == 90
    assert result.service_call_performed is False
    remaining = {record.attempt_id for record in await repo.async_list()}
    assert remaining == {"old-prepared", "old-recovery", "young-no-lifecycle"}


@pytest.mark.asyncio
async def test_transactional_remove_store_failure_keeps_original_pending_ledger() -> None:
    store = _Store()
    repo = ExecutionPendingRunRepository(store)
    first = _pending(
        "first",
        ends_at=NOW - timedelta(days=PENDING_RUN_RETENTION_DAYS + 1),
    )
    second = _pending(
        "second",
        ends_at=NOW - timedelta(days=PENDING_RUN_RETENTION_DAYS + 1),
    )
    await repo.async_record(first)
    await repo.async_record(second)
    store.fail = True

    with pytest.raises(RuntimeError, match="pending retention store unavailable"):
        await repo.async_remove_attempt_ids({"first"})

    assert {record.attempt_id for record in await repo.async_list()} == {"first", "second"}


@pytest.mark.asyncio
async def test_best_effort_retention_failure_is_visible_but_non_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(*args: Any, **kwargs: Any):
        raise RuntimeError("cleanup store offline")

    monkeypatch.setattr(retention_runtime, "async_prune_pending_run_audit", fail)
    hass = _Hass()

    first = await retention_runtime.async_run_pending_run_retention_best_effort(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        now=NOW,
    )
    second = await retention_runtime.async_run_pending_run_retention_best_effort(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        now=NOW,
    )

    assert first.status == "failed_non_blocking"
    assert first.runs == 1
    assert first.pruned_total == 0
    assert first.last_error == "cleanup store offline"
    assert second.status == "failed_non_blocking"
    assert second.runs == 2
    assert second.pruned_total == 0
    assert second.last_error == "cleanup store offline"
