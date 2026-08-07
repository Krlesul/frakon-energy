import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_recovery_verification as recovery_verification
from custom_components.frakon_energy import load_execution_start_dispatcher as start_dispatcher
from custom_components.frakon_energy import load_execution_start_scheduler as start_scheduler_mod
from custom_components.frakon_energy import load_execution_start_stop_ownership as ownership_mod
from custom_components.frakon_energy import load_execution_stop_dispatcher as stop_dispatcher
from custom_components.frakon_energy import load_execution_stop_resolution as stop_resolution
from custom_components.frakon_energy import load_execution_stop_scheduler as stop_scheduler_mod
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_bounded_dispatch_gate import (
    BOUNDED_GATE_READY,
    BoundedDispatchDecision,
)
from custom_components.frakon_energy.load_execution_lifecycle import (
    CALL_UNKNOWN,
    STATE_RECOVERY_REQUIRED,
    STATE_VERIFIED,
    ExecutionLifecycleRecord,
    ExecutionLifecycleRepository,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import evaluate_execution_readiness
from custom_components.frakon_energy.load_execution_start_scheduler import (
    STATUS_RECOVERY_REVIEW,
    STATUS_STARTED_VERIFIED,
    STATUS_VERIFIED,
    ExecutionStartScheduler,
)
from custom_components.frakon_energy.load_execution_stop_lease import (
    ExecutionStopLease,
    ExecutionStopLeaseRepository,
)
from custom_components.frakon_energy.load_execution_stop_lifecycle import (
    STOP_STATE_OWNED,
    STOP_STATE_VERIFIED,
    ExecutionStopLifecycleRepository,
)
from custom_components.frakon_energy.load_execution_stop_scheduler import (
    STATUS_VERIFIED as STOP_SCHEDULER_VERIFIED,
    ExecutionStopScheduler,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)
END = START + timedelta(hours=2)


class _Store:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = data


class _States:
    def __init__(self) -> None:
        self.value = "off"

    def get(self, entity_id: str) -> object:
        return SimpleNamespace(state=self.value)


class _Services:
    def __init__(self, states: _States) -> None:
        self.states = states
        self.calls: list[dict[str, Any]] = []
        self.raise_after_first_turn_on = False
        self._turn_on_raised = False

    async def async_call(
        self,
        domain: str,
        service: str,
        service_data: dict[str, Any] | None = None,
        blocking: bool = False,
        context: Any = None,
        target: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.calls.append(
            {
                "domain": domain,
                "service": service,
                "service_data": service_data,
                "blocking": blocking,
                "context": context,
                "target": target,
            }
        )
        if service == "turn_on":
            self.states.value = "on"
            if self.raise_after_first_turn_on and not self._turn_on_raised:
                self._turn_on_raised = True
                raise RuntimeError("start transport confirmation lost")
        elif service == "turn_off":
            self.states.value = "off"
        else:
            raise AssertionError(f"unexpected physical service: {domain}.{service}")


class _Hass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.states = _States()
        self.services = _Services(self.states)
        self.tasks: list[asyncio.Task[Any]] = []

    def async_create_task(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task


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
    power = 11.0
    duration = 120
    energy = power * duration / 60
    return LoadPlan(
        load_id="ev-home",
        name="Enyaq",
        starts_at=START.isoformat(),
        ends_at=END.isoformat(),
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
    action_snapshot = ExecutionActionSnapshot.from_attempt_and_intent(
        attempt=attempt,
        intent=resolve_start_action_intent(profile),
        created_at=attempt.created_at,
    )
    readiness = evaluate_execution_readiness(
        attempt=attempt,
        snapshot=action_snapshot,
        profile=profile,
        plan=plan,
        policy=policy,
        current_state="off",
        now=START,
    )
    return ExecutionLifecycleRecord.prepared(
        attempt=attempt,
        action_snapshot=action_snapshot,
        plan=plan,
        readiness=readiness,
        created_at=int(START.timestamp()),
    )


def _bounded_gate_payload(start: ExecutionLifecycleRecord, lease: ExecutionStopLease) -> dict[str, Any]:
    decision = BoundedDispatchDecision(
        status=BOUNDED_GATE_READY,
        reason="bounded_start_has_armed_stop_obligation",
        lifecycle_id=start.lifecycle_id,
        attempt_id=start.attempt_id,
        entity_id=start.entity_id,
        start_service_domain=start.service_domain,
        start_service_name=start.service_name,
        stop_lease_id=lease.lease_id,
        stop_intent_id=lease.stop_intent_id,
        stop_service_domain=lease.service_domain,
        stop_service_name=lease.service_name,
        stop_at=start.plan.ends_at,
        dispatch_gate_status="ready_to_dispatch",
        dispatch_gate_matches=True,
        stop_lease_matches=True,
        can_start=True,
    )
    return {
        "lifecycle": start.as_dict(),
        "stop_lease": lease.as_dict(),
        "bounded_dispatch_gate": decision.as_dict(),
    }


async def _repositories():
    start_repo = ExecutionLifecycleRepository(_Store())
    prepared = (await start_repo.async_prepare(_prepared())).record
    lease_repo = ExecutionStopLeaseRepository(_Store())
    lease = ExecutionStopLease.from_prepared_lifecycle(
        prepared,
        created_at=prepared.updated_at,
    )
    await lease_repo.async_record(lease)
    stop_repo = ExecutionStopLifecycleRepository(_Store())
    return prepared, lease, start_repo, lease_repo, stop_repo


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    hass: _Hass,
    *,
    prepared: ExecutionLifecycleRecord,
    lease: ExecutionStopLease,
    start_repo: ExecutionLifecycleRepository,
    lease_repo: ExecutionStopLeaseRepository,
    stop_repo: ExecutionStopLifecycleRepository,
    stop_scheduler: ExecutionStopScheduler,
    timers: list[tuple[Any, datetime]],
) -> None:
    # Shared durable repositories across every execution layer.
    monkeypatch.setattr(start_scheduler_mod, "lifecycle_repository", lambda hass, entry_id: start_repo)
    monkeypatch.setattr(start_dispatcher, "lifecycle_repository", lambda hass, entry_id: start_repo)
    monkeypatch.setattr(recovery_verification, "lifecycle_repository", lambda hass, entry_id: start_repo)
    monkeypatch.setattr(start_dispatcher, "stop_lifecycle_repository", lambda hass, entry_id: stop_repo)
    monkeypatch.setattr(stop_scheduler_mod, "stop_lifecycle_repository", lambda hass, entry_id: stop_repo)
    monkeypatch.setattr(stop_dispatcher, "stop_lifecycle_repository", lambda hass, entry_id: stop_repo)
    monkeypatch.setattr(stop_resolution, "stop_lifecycle_repository", lambda hass, entry_id: stop_repo)
    monkeypatch.setattr(ownership_mod, "stop_lifecycle_repository", lambda hass, entry_id: stop_repo)
    monkeypatch.setattr(ownership_mod, "stop_lease_repository", lambda hass, entry_id: lease_repo)

    # Recovery/runtime health is green; these tests focus the cross-layer run.
    ok = SimpleNamespace(status="ok")
    monkeypatch.setattr(start_scheduler_mod, "lifecycle_recovery_summary", lambda hass, entry_id: ok)
    monkeypatch.setattr(start_scheduler_mod, "stop_recovery_summary", lambda hass, entry_id: ok)
    monkeypatch.setattr(start_scheduler_mod, "stop_scheduler", lambda hass, entry_id: stop_scheduler)
    monkeypatch.setattr(start_dispatcher, "assert_lifecycle_recovery_ready", lambda hass, entry_id: None)
    monkeypatch.setattr(start_dispatcher, "assert_stop_recovery_ready", lambda hass, entry_id: None)
    monkeypatch.setattr(start_dispatcher, "stop_scheduler", lambda hass, entry_id: stop_scheduler)
    monkeypatch.setattr(recovery_verification, "assert_lifecycle_recovery_ready", lambda hass, entry_id: None)
    monkeypatch.setattr(stop_dispatcher, "assert_stop_recovery_ready", lambda hass, entry_id: None)
    monkeypatch.setattr(stop_resolution, "assert_stop_recovery_ready", lambda hass, entry_id: None)
    monkeypatch.setattr(stop_scheduler_mod, "stop_recovery_summary", lambda hass, entry_id: ok)

    async def bounded_gate(hass_obj, *, entry_id, attempt_id, now):
        current = await start_repo.async_get_by_attempt_id(attempt_id)
        assert current is not None
        return _bounded_gate_payload(current, lease)

    monkeypatch.setattr(start_scheduler_mod, "async_bounded_dispatch_gate", bounded_gate)
    monkeypatch.setattr(start_dispatcher, "async_bounded_dispatch_gate", bounded_gate)

    async def refresh_stop(hass_obj, entry_id):
        if stop_scheduler.started:
            await stop_scheduler.async_refresh(now=START)

    monkeypatch.setattr(start_dispatcher, "async_refresh_stop_scheduler_if_started", refresh_stop)
    monkeypatch.setattr(stop_dispatcher, "async_refresh_stop_scheduler_if_started", lambda *args, **kwargs: None)

    def track(hass_obj, action, when):
        timers.append((action, when))
        return lambda: None

    monkeypatch.setattr(stop_scheduler_mod, "async_track_point_in_utc_time", track)


def _physical_services(calls: list[dict[str, Any]]) -> list[str]:
    return [str(item["service"]) for item in calls]


@pytest.mark.asyncio
async def test_full_autonomous_bounded_run_turns_on_once_then_off_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, lease, start_repo, lease_repo, stop_repo = await _repositories()
    hass = _Hass()
    timers: list[tuple[Any, datetime]] = []
    stop_runtime = ExecutionStopScheduler(hass, "entry-1")  # type: ignore[arg-type]
    stop_runtime._started = True
    _wire(
        monkeypatch,
        hass,
        prepared=prepared,
        lease=lease,
        start_repo=start_repo,
        lease_repo=lease_repo,
        stop_repo=stop_repo,
        stop_scheduler=stop_runtime,
        timers=timers,
    )
    start_runtime = ExecutionStartScheduler(hass, "entry-1")  # type: ignore[arg-type]
    start_runtime._started = True

    await start_runtime.async_refresh(now=START)

    assert _physical_services(hass.services.calls) == ["turn_on"]
    assert hass.states.value == "on"
    start = await start_repo.async_get_by_attempt_id("attempt-1")
    stop = await stop_repo.async_get_by_start_lifecycle_id(prepared.lifecycle_id)
    assert start is not None and start.state == STATE_VERIFIED
    assert stop is not None and stop.state == STOP_STATE_OWNED
    assert start_runtime.statuses()[0].status == STATUS_STARTED_VERIFIED
    assert timers and timers[-1][1] == END.astimezone(timezone.utc)

    timers[-1][0](END)
    await asyncio.gather(*hass.tasks)

    assert _physical_services(hass.services.calls) == ["turn_on", "turn_off"]
    assert hass.states.value == "off"
    stop = await stop_repo.async_get_by_start_lifecycle_id(prepared.lifecycle_id)
    assert stop is not None and stop.state == STOP_STATE_VERIFIED
    assert stop_runtime.statuses()[0].status == STOP_SCHEDULER_VERIFIED
    assert hass.services.calls[0]["target"] == {"entity_id": prepared.entity_id}
    assert hass.services.calls[1]["target"] == {"entity_id": prepared.entity_id}
    assert all(call["service_data"] == {} for call in hass.services.calls)
    assert all(call["blocking"] is True for call in hass.services.calls)


@pytest.mark.asyncio
async def test_lost_start_confirmation_recovers_without_second_turn_on_and_still_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, lease, start_repo, lease_repo, stop_repo = await _repositories()
    hass = _Hass()
    hass.services.raise_after_first_turn_on = True
    timers: list[tuple[Any, datetime]] = []
    stop_runtime = ExecutionStopScheduler(hass, "entry-1")  # type: ignore[arg-type]
    stop_runtime._started = True
    _wire(
        monkeypatch,
        hass,
        prepared=prepared,
        lease=lease,
        start_repo=start_repo,
        lease_repo=lease_repo,
        stop_repo=stop_repo,
        stop_scheduler=stop_runtime,
        timers=timers,
    )
    first_runtime = ExecutionStartScheduler(hass, "entry-1")  # type: ignore[arg-type]
    first_runtime._started = True

    await first_runtime.async_refresh(now=START)

    assert _physical_services(hass.services.calls) == ["turn_on"]
    assert hass.states.value == "on"
    recovered = await start_repo.async_get_by_attempt_id("attempt-1")
    stop = await stop_repo.async_get_by_start_lifecycle_id(prepared.lifecycle_id)
    assert recovered is not None and recovered.state == STATE_RECOVERY_REQUIRED
    assert recovered.service_call_status == CALL_UNKNOWN
    assert stop is not None and stop.state == STOP_STATE_OWNED
    assert first_runtime.statuses()[0].status == STATUS_RECOVERY_REVIEW

    # Simulated process restart: runtime state is reconstructed from durable stores.
    restarted_stop = ExecutionStopScheduler(hass, "entry-1")  # type: ignore[arg-type]
    restarted_stop._started = True
    monkeypatch.setattr(start_scheduler_mod, "stop_scheduler", lambda hass, entry_id: restarted_stop)
    monkeypatch.setattr(start_dispatcher, "stop_scheduler", lambda hass, entry_id: restarted_stop)

    async def refresh_restarted_stop(hass_obj, entry_id):
        if restarted_stop.started:
            await restarted_stop.async_refresh(now=START + timedelta(seconds=1))

    monkeypatch.setattr(start_dispatcher, "async_refresh_stop_scheduler_if_started", refresh_restarted_stop)
    await restarted_stop.async_refresh(now=START + timedelta(seconds=1))
    restarted_start = ExecutionStartScheduler(hass, "entry-1")  # type: ignore[arg-type]
    restarted_start._started = True

    await restarted_start.async_refresh(now=START + timedelta(seconds=1))

    assert _physical_services(hass.services.calls) == ["turn_on"]
    verified = await start_repo.async_get_by_attempt_id("attempt-1")
    assert verified is not None and verified.state == STATE_VERIFIED
    assert verified.service_call_status == CALL_UNKNOWN
    assert restarted_start.statuses()[0].status == STATUS_VERIFIED
    assert timers

    timers[-1][0](END)
    await asyncio.gather(*hass.tasks)

    assert _physical_services(hass.services.calls) == ["turn_on", "turn_off"]
    final_stop = await stop_repo.async_get_by_start_lifecycle_id(prepared.lifecycle_id)
    assert final_stop is not None and final_stop.state == STOP_STATE_VERIFIED
