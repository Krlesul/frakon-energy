from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_bounded_dispatch_gate_ws_api as gate_ws
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_bounded_dispatch_gate import (
    BOUNDED_GATE_BLOCKED,
    BOUNDED_GATE_READY,
    REASON_STOP_LEASE_REQUIRED,
)
from custom_components.frakon_energy.load_execution_capacity_guard import (
    GUARD_BLOCKED,
    GUARD_DISABLED,
    REASON_DISABLED,
    REASON_INSUFFICIENT_HEADROOM,
    SiteCapacityStartDecision,
)
from custom_components.frakon_energy.load_execution_dispatch_gate import evaluate_dispatch_gate
from custom_components.frakon_energy.load_execution_lifecycle import ExecutionLifecycleRecord
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import evaluate_execution_readiness
from custom_components.frakon_energy.load_execution_stop_lease import (
    ExecutionStopLease,
    ExecutionStopLeaseRepository,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)


class _FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.saves = 0

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
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


def _plan() -> LoadPlan:
    duration = 120
    power = 11.0
    average = 2.0
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


def _evidence(current_state: str = "off"):
    profile = _profile()
    policy = _policy()
    plan = _plan()
    attempt = ExecutionAttempt(
        attempt_id="attempt-1",
        entry_id="entry-1",
        profile_id=profile.profile_id,
        entity_id=profile.entity_id,
        approval_id="approval-1",
        approval_fingerprint="a" * 64,
        snapshot_digest=execution_snapshot_digest(profile, plan, policy),
        intent="execute_load_plan",
        approval_issued_at=int((START - timedelta(minutes=5)).timestamp()),
        approval_expires_at=int((START + timedelta(minutes=5)).timestamp()),
        created_at=int((START - timedelta(minutes=1)).timestamp()),
    ).validated()
    snapshot = ExecutionActionSnapshot.from_attempt_and_intent(
        attempt=attempt,
        intent=resolve_start_action_intent(profile),
        created_at=attempt.created_at,
    )
    prepared_readiness = evaluate_execution_readiness(
        attempt=attempt,
        snapshot=snapshot,
        profile=profile,
        plan=plan,
        policy=policy,
        current_state="off",
        now=START,
    )
    lifecycle = ExecutionLifecycleRecord.prepared(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=plan,
        readiness=prepared_readiness,
        created_at=int(START.timestamp()),
    )
    readiness = evaluate_execution_readiness(
        attempt=attempt,
        snapshot=snapshot,
        profile=profile,
        plan=plan,
        policy=policy,
        current_state=current_state,
        now=START,
    )
    dispatch_gate = evaluate_dispatch_gate(
        lifecycle=lifecycle,
        attempt=attempt,
        snapshot=snapshot,
        readiness=readiness,
    )
    return lifecycle, dispatch_gate


def _capacity_decision(*, blocked: bool = False) -> SiteCapacityStartDecision:
    if blocked:
        return SiteCapacityStartDecision(
            status=GUARD_BLOCKED,
            reason=REASON_INSUFFICIENT_HEADROOM,
            entry_id="entry-1",
            guard_enabled=True,
            guard_applies=True,
            additional_power_kw=11.0,
            max_grid_import_kw=12.0,
            current_grid_import_kw=5.0,
            projected_grid_import_kw=16.0,
            headroom_before_kw=7.0,
            headroom_after_kw=0.0,
            projected_over_limit_kw=4.0,
            source_entity_id="sensor.grid_in",
            can_start=False,
        )
    return SiteCapacityStartDecision(
        status=GUARD_DISABLED,
        reason=REASON_DISABLED,
        entry_id="entry-1",
        guard_enabled=False,
        guard_applies=False,
        additional_power_kw=11.0,
        max_grid_import_kw=None,
        current_grid_import_kw=None,
        projected_grid_import_kw=None,
        headroom_before_kw=None,
        headroom_after_kw=None,
        projected_over_limit_kw=None,
        source_entity_id=None,
        can_start=True,
    )


def _patch_capacity(monkeypatch: pytest.MonkeyPatch, *, blocked: bool = False) -> None:
    monkeypatch.setattr(
        gate_ws,
        "evaluate_site_capacity_start",
        lambda *args, **kwargs: _capacity_decision(blocked=blocked),
    )


@pytest.mark.asyncio
async def test_endpoint_requires_matching_persisted_stop_lease_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle, dispatch_gate = _evidence()
    store = _FakeStore()
    repository = ExecutionStopLeaseRepository(store)
    lease = ExecutionStopLease.from_prepared_lifecycle(
        lifecycle,
        created_at=lifecycle.created_at,
    )
    await repository.async_record(lease)
    before_saves = store.saves

    async def dispatch_gate_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "lifecycle": lifecycle.as_dict(),
            "dispatch_gate": dispatch_gate.as_dict(),
        }

    monkeypatch.setattr(gate_ws, "async_execution_dispatch_gate", dispatch_gate_call)
    monkeypatch.setattr(
        gate_ws,
        "stop_lease_repository",
        lambda hass, entry_id: repository,
    )
    _patch_capacity(monkeypatch)

    result = await gate_ws.async_bounded_dispatch_gate(
        SimpleNamespace(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START,
    )

    assert result["bounded_dispatch_gate"]["status"] == BOUNDED_GATE_READY
    assert result["bounded_dispatch_gate"]["can_start"] is True
    assert result["site_capacity_guard"]["status"] == GUARD_DISABLED
    assert result["stop_lease"] == lease.as_dict()
    assert result["read_only"] is True
    assert result["state_transition_performed"] is False
    assert result["service_call_performed"] is False
    assert result["execution_performed"] is False
    assert result["executor_available"] is False
    assert store.saves == before_saves


@pytest.mark.asyncio
async def test_endpoint_blocks_ready_dispatch_when_stop_lease_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle, dispatch_gate = _evidence()
    repository = ExecutionStopLeaseRepository(_FakeStore())

    async def dispatch_gate_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "lifecycle": lifecycle.as_dict(),
            "dispatch_gate": dispatch_gate.as_dict(),
        }

    monkeypatch.setattr(gate_ws, "async_execution_dispatch_gate", dispatch_gate_call)
    monkeypatch.setattr(
        gate_ws,
        "stop_lease_repository",
        lambda hass, entry_id: repository,
    )
    _patch_capacity(monkeypatch)

    result = await gate_ws.async_bounded_dispatch_gate(
        SimpleNamespace(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START,
    )

    assert result["bounded_dispatch_gate"]["status"] == BOUNDED_GATE_BLOCKED
    assert result["bounded_dispatch_gate"]["reason"] == REASON_STOP_LEASE_REQUIRED
    assert result["bounded_dispatch_gate"]["can_start"] is False
    assert result["stop_lease"] is None


@pytest.mark.asyncio
async def test_capacity_guard_can_block_an_otherwise_ready_bounded_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle, dispatch_gate = _evidence()
    repository = ExecutionStopLeaseRepository(_FakeStore())
    lease = ExecutionStopLease.from_prepared_lifecycle(
        lifecycle,
        created_at=lifecycle.created_at,
    )
    await repository.async_record(lease)

    async def dispatch_gate_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "lifecycle": lifecycle.as_dict(),
            "dispatch_gate": dispatch_gate.as_dict(),
        }

    monkeypatch.setattr(gate_ws, "async_execution_dispatch_gate", dispatch_gate_call)
    monkeypatch.setattr(gate_ws, "stop_lease_repository", lambda hass, entry_id: repository)
    _patch_capacity(monkeypatch, blocked=True)

    result = await gate_ws.async_bounded_dispatch_gate(
        SimpleNamespace(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START,
    )

    assert result["site_capacity_guard"]["status"] == GUARD_BLOCKED
    assert result["site_capacity_guard"]["reason"] == REASON_INSUFFICIENT_HEADROOM
    assert result["bounded_dispatch_gate"]["status"] == BOUNDED_GATE_BLOCKED
    assert result["bounded_dispatch_gate"]["reason"] == "site_capacity:capacity_insufficient_headroom"
    assert result["bounded_dispatch_gate"]["can_start"] is False
    assert result["service_call_performed"] is False
