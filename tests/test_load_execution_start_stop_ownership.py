from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.frakon_energy import load_execution_start_stop_ownership as ownership
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import (
    ExecutionLifecycleRecord,
    begin_dispatch,
    require_recovery_after_restart,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import evaluate_execution_readiness
from custom_components.frakon_energy.load_execution_stop_lease import ExecutionStopLease
from custom_components.frakon_energy.load_execution_stop_lifecycle import (
    ExecutionStopLifecycleRecord,
    fail_stop_lifecycle,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)


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


def _prepared() -> ExecutionLifecycleRecord:
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
    readiness = evaluate_execution_readiness(
        attempt=attempt,
        snapshot=snapshot,
        profile=profile,
        plan=plan,
        policy=policy,
        current_state="off",
        now=START,
    )
    return ExecutionLifecycleRecord.prepared(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=plan,
        readiness=readiness,
        created_at=int(START.timestamp()),
    )


def _records():
    prepared = _prepared()
    lease = ExecutionStopLease.from_prepared_lifecycle(
        prepared,
        created_at=prepared.updated_at,
    )
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)
    stop = ExecutionStopLifecycleRecord.owned(
        lease=lease,
        start_lifecycle=dispatching,
        created_at=dispatching.updated_at,
    )
    recovered = require_recovery_after_restart(
        dispatching,
        now=dispatching.updated_at + 1,
    )
    return recovered, lease, stop


class _LeaseRepo:
    def __init__(self, lease):
        self.lease = lease

    async def async_get_by_lifecycle_id(self, lifecycle_id):
        return self.lease if self.lease and self.lease.lifecycle_id == lifecycle_id else None


class _StopRepo:
    def __init__(self, stop):
        self.stop = stop

    async def async_get_by_start_lifecycle_id(self, lifecycle_id):
        return self.stop if self.stop and self.stop.start_lifecycle_id == lifecycle_id else None


def _wire(monkeypatch, lease, stop):
    monkeypatch.setattr(
        ownership,
        "stop_lease_repository",
        lambda hass, entry_id: _LeaseRepo(lease),
    )
    monkeypatch.setattr(
        ownership,
        "stop_lifecycle_repository",
        lambda hass, entry_id: _StopRepo(stop),
    )


@pytest.mark.asyncio
async def test_matching_stop_lease_and_lifecycle_prove_bounded_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start, lease, stop = _records()
    _wire(monkeypatch, lease, stop)
    proof = await ownership.async_start_stop_ownership_proof(
        object(),  # type: ignore[arg-type]
        entry_id="entry-1",
        start=start,
    )
    assert proof.ownership_ready is True
    assert proof.reason == "stop_ownership_ready"


@pytest.mark.asyncio
async def test_failed_stop_lifecycle_never_proves_bounded_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start, lease, stop = _records()
    failed = fail_stop_lifecycle(
        stop,
        reason="stop runtime unavailable before start",
        now=stop.updated_at + 1,
    )
    _wire(monkeypatch, lease, failed)

    proof = await ownership.async_start_stop_ownership_proof(
        object(),  # type: ignore[arg-type]
        entry_id="entry-1",
        start=start,
    )

    assert proof.stop_lifecycle_present is True
    assert proof.stop_lifecycle_matches is False
    assert proof.ownership_ready is False
    assert proof.reason == "stop_lifecycle_binding_mismatch"


@pytest.mark.asyncio
async def test_missing_stop_lease_blocks_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    start, _, stop = _records()
    _wire(monkeypatch, None, stop)
    proof = await ownership.async_start_stop_ownership_proof(
        object(),  # type: ignore[arg-type]
        entry_id="entry-1",
        start=start,
    )
    assert proof.ownership_ready is False
    assert proof.reason == "stop_lease_missing"


@pytest.mark.asyncio
async def test_missing_stop_lifecycle_blocks_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    start, lease, _ = _records()
    _wire(monkeypatch, lease, None)
    proof = await ownership.async_start_stop_ownership_proof(
        object(),  # type: ignore[arg-type]
        entry_id="entry-1",
        start=start,
    )
    assert proof.stop_lease_matches is True
    assert proof.stop_lifecycle_present is False
    assert proof.ownership_ready is False
    assert proof.reason == "stop_lifecycle_missing"


@pytest.mark.asyncio
async def test_tampered_stop_lifecycle_binding_blocks_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start, lease, stop = _records()
    tampered = replace(stop, profile_id="other-profile")
    _wire(monkeypatch, lease, tampered)
    proof = await ownership.async_start_stop_ownership_proof(
        object(),  # type: ignore[arg-type]
        entry_id="entry-1",
        start=start,
    )
    assert proof.ownership_ready is False
    assert proof.reason == "stop_lifecycle_binding_mismatch"


@pytest.mark.asyncio
async def test_entry_mismatch_fails_before_cross_store_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start, lease, stop = _records()
    _wire(monkeypatch, lease, stop)
    proof = await ownership.async_start_stop_ownership_proof(
        object(),  # type: ignore[arg-type]
        entry_id="other-entry",
        start=start,
    )
    assert proof.ownership_ready is False
    assert proof.reason == "entry_id_mismatch"
