from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_stop_lease_ws_api as lease_ws
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_dispatch_gate import (
    DISPATCH_GATE_ALREADY_SATISFIED,
    DISPATCH_GATE_READY,
)
from custom_components.frakon_energy.load_execution_lifecycle import (
    STATE_PREPARED,
    ExecutionLifecycleRecord,
    ExecutionLifecycleRepository,
    begin_dispatch,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import evaluate_execution_readiness
from custom_components.frakon_energy.load_execution_stop_lease import (
    ExecutionStopLeaseRepository,
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


class _FakeHass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}


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


async def _repositories():
    lifecycle_store = _FakeStore()
    lease_store = _FakeStore()
    lifecycles = ExecutionLifecycleRepository(lifecycle_store)
    leases = ExecutionStopLeaseRepository(lease_store)
    await lifecycles.async_prepare(_prepared())
    return lifecycle_store, lease_store, lifecycles, leases


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    lifecycles: ExecutionLifecycleRepository,
    leases: ExecutionStopLeaseRepository,
    *,
    gate_status: str = DISPATCH_GATE_READY,
    gate_calls: list[int] | None = None,
    gate_hook=None,
) -> None:
    monkeypatch.setattr(
        lease_ws,
        "lifecycle_repository",
        lambda hass, entry_id: lifecycles,
    )
    monkeypatch.setattr(
        lease_ws,
        "stop_lease_repository",
        lambda hass, entry_id: leases,
    )

    async def gate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if gate_calls is not None:
            gate_calls.append(1)
        if gate_hook is not None:
            await gate_hook()
        lifecycle = await lifecycles.async_get_by_attempt_id("attempt-1")
        assert lifecycle is not None
        return {
            "dispatch_gate": {
                "status": gate_status,
                "reason": "test",
                "can_dispatch": gate_status == DISPATCH_GATE_READY,
                "can_redispatch": False,
                "lifecycle_id": lifecycle.lifecycle_id,
            }
        }

    monkeypatch.setattr(lease_ws, "async_execution_dispatch_gate", gate)


@pytest.mark.asyncio
async def test_ready_dispatch_prepares_exact_stop_obligation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle_store, lease_store, lifecycles, leases = await _repositories()
    _wire(monkeypatch, lifecycles, leases)
    lifecycle_before = await lifecycles.async_get_by_attempt_id("attempt-1")
    lifecycle_saves = lifecycle_store.saves

    result = await lease_ws.async_prepare_stop_lease(
        _FakeHass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START,
    )

    lifecycle_after = await lifecycles.async_get_by_attempt_id("attempt-1")
    assert result["created"] is True
    assert result["idempotent_replay"] is False
    assert result["stop_obligation_armed"] is True
    assert result["can_start_without_stop_lease"] is False
    assert result["stop_lease"]["service_domain"] == "switch"
    assert result["stop_lease"]["service_name"] == "turn_off"
    assert result["stop_lease"]["entity_id"] == "switch.enyaq_charging"
    assert result["stop_lease"]["ends_at"] == _plan().ends_at
    assert result["service_call_performed"] is False
    assert result["execution_performed"] is False
    assert result["executor_available"] is False
    assert lease_store.saves == 1
    assert lifecycle_store.saves == lifecycle_saves
    assert lifecycle_after == lifecycle_before


@pytest.mark.asyncio
async def test_already_satisfied_does_not_create_stop_obligation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, lease_store, lifecycles, leases = await _repositories()
    _wire(
        monkeypatch,
        lifecycles,
        leases,
        gate_status=DISPATCH_GATE_ALREADY_SATISFIED,
    )

    with pytest.raises(lease_ws.StopLeasePrepareError, match="not ready"):
        await lease_ws.async_prepare_stop_lease(
            _FakeHass(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START,
        )

    assert await leases.async_list() == ()
    assert lease_store.saves == 0


@pytest.mark.asyncio
async def test_exact_retry_reuses_lease_without_rerunning_dispatch_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, lease_store, lifecycles, leases = await _repositories()
    calls: list[int] = []
    _wire(monkeypatch, lifecycles, leases, gate_calls=calls)
    hass = _FakeHass()
    first = await lease_ws.async_prepare_stop_lease(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START,
    )
    before_saves = lease_store.saves

    retry = await lease_ws.async_prepare_stop_lease(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START + timedelta(minutes=10),
    )

    assert retry["stop_lease"] == first["stop_lease"]
    assert retry["created"] is False
    assert retry["idempotent_replay"] is True
    assert len(calls) == 1
    assert lease_store.saves == before_saves


@pytest.mark.asyncio
async def test_lifecycle_change_during_gate_rejects_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, lifecycles, leases = await _repositories()

    async def mutate_lifecycle() -> None:
        prepared = await lifecycles.async_get_by_attempt_id("attempt-1")
        assert prepared is not None
        if prepared.state == STATE_PREPARED:
            await lifecycles.async_update(
                begin_dispatch(prepared, now=prepared.updated_at + 1)
            )

    _wire(monkeypatch, lifecycles, leases, gate_hook=mutate_lifecycle)

    with pytest.raises(lease_ws.StopLeasePrepareError, match="lifecycle changed"):
        await lease_ws.async_prepare_stop_lease(
            _FakeHass(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START,
        )

    assert await leases.async_list() == ()


@pytest.mark.asyncio
async def test_stop_lease_store_failure_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, lease_store, lifecycles, leases = await _repositories()
    _wire(monkeypatch, lifecycles, leases)
    lease_store.fail_save = True

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await lease_ws.async_prepare_stop_lease(
            _FakeHass(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START,
        )

    assert await leases.async_list() == ()
    assert lease_store.saves == 0


@pytest.mark.asyncio
async def test_stop_lease_list_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, lifecycles, leases = await _repositories()
    _wire(monkeypatch, lifecycles, leases)
    hass = _FakeHass()
    await lease_ws.async_prepare_stop_lease(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START,
    )

    result = await lease_ws.async_list_stop_leases(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert len(result["stop_leases"]) == 1
    assert result["read_only"] is True
    assert result["service_call_performed"] is False
    assert result["execution_performed"] is False
    assert result["executor_available"] is False
