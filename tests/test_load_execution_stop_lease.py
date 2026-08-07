from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_lifecycle import (
    ExecutionLifecycleRecord,
    begin_dispatch,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import evaluate_execution_readiness
from custom_components.frakon_energy.load_execution_stop_lease import (
    STOP_LEASE_ARMED,
    ExecutionStopLease,
    ExecutionStopLeaseLedger,
    ExecutionStopLeaseRepository,
    StopLeaseError,
    stop_lease_storage_key,
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


def _lease(*, created_at: int | None = None) -> ExecutionStopLease:
    lifecycle = _prepared()
    return ExecutionStopLease.from_prepared_lifecycle(
        lifecycle,
        created_at=created_at if created_at is not None else lifecycle.created_at,
    )


def test_switch_start_maps_to_exact_turn_off_obligation() -> None:
    lease = _lease()

    assert lease.status == STOP_LEASE_ARMED
    assert lease.entity_id == "switch.enyaq_charging"
    assert lease.service_domain == "switch"
    assert lease.service_name == "turn_off"
    assert lease.desired_state == "off"
    assert lease.starts_at == _plan().starts_at
    assert lease.ends_at == _plan().ends_at
    assert lease.service_call_performed is False
    assert lease.executor_available is False


def test_lease_identity_is_deterministic_across_retry_timestamp() -> None:
    first = _lease(created_at=int(START.timestamp()))
    second = _lease(created_at=int(START.timestamp()) + 10)

    assert first.lease_id == second.lease_id
    assert first.stop_intent_id == second.stop_intent_id


def test_non_prepared_lifecycle_cannot_create_stop_lease() -> None:
    prepared = _prepared()
    dispatching = begin_dispatch(prepared, now=prepared.updated_at + 1)

    with pytest.raises(StopLeaseError, match="requires a prepared lifecycle"):
        ExecutionStopLease.from_prepared_lifecycle(
            dispatching,
            created_at=dispatching.updated_at,
        )


def test_tampered_stop_service_is_rejected() -> None:
    raw = _lease().as_dict()
    raw["service_name"] = "toggle"

    with pytest.raises(StopLeaseError, match="not allowlisted"):
        ExecutionStopLease.from_dict(raw)


def test_tampered_end_time_breaks_stop_intent_identity() -> None:
    raw = _lease().as_dict()
    raw["ends_at"] = (START + timedelta(hours=3)).isoformat()

    with pytest.raises(StopLeaseError, match="stop intent identity"):
        ExecutionStopLease.from_dict(raw)


def test_ledger_is_idempotent_for_same_immutable_lease() -> None:
    ledger = ExecutionStopLeaseLedger()
    first = ledger.record(_lease(created_at=100))
    retry = ledger.record(_lease(created_at=200))

    assert first.created is True
    assert retry.created is False
    assert retry.idempotent_replay is True
    assert retry.lease.lease_id == first.lease.lease_id
    assert len(ledger.leases) == 1


def test_storage_round_trip_preserves_lease() -> None:
    ledger = ExecutionStopLeaseLedger()
    lease = _lease()
    ledger.record(lease)

    restored = ExecutionStopLeaseLedger.from_storage(ledger.as_storage())

    assert restored.leases == (lease,)
    assert restored.get_by_lifecycle_id(lease.lifecycle_id) == lease


@pytest.mark.asyncio
async def test_repository_persists_once_and_retry_is_idempotent() -> None:
    store = _FakeStore()
    repository = ExecutionStopLeaseRepository(store)

    first = await repository.async_record(_lease(created_at=100))
    retry = await repository.async_record(_lease(created_at=200))

    assert first.created is True
    assert retry.idempotent_replay is True
    assert store.saves == 1
    assert len(await repository.async_list()) == 1


@pytest.mark.asyncio
async def test_repository_rolls_back_when_store_fails() -> None:
    store = _FakeStore()
    store.fail_save = True
    repository = ExecutionStopLeaseRepository(store)

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await repository.async_record(_lease())

    assert await repository.async_list() == ()
    assert store.saves == 0


def test_storage_key_is_stable_and_isolated_per_entry() -> None:
    first = stop_lease_storage_key("entry-1")
    again = stop_lease_storage_key("entry-1")
    other = stop_lease_storage_key("entry-2")

    assert first == again
    assert first != other
    assert first.startswith("frakon_energy.load_execution_stop_leases.")
