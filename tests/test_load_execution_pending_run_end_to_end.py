from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_pending_run_scheduler as pending_scheduler_mod
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
    STATE_PREPARED,
    STATE_VERIFIED,
    ExecutionLifecycleRecord,
    ExecutionLifecycleRepository,
    ExecutionPlanSnapshot,
)
from custom_components.frakon_energy.load_execution_pending_run import (
    ExecutionPendingRun,
    ExecutionPendingRunRepository,
)
from custom_components.frakon_energy.load_execution_pending_run_scheduler import (
    STATUS_DELEGATED,
    STATUS_PREPARED_WITH_STOP_LEASE,
    ExecutionPendingRunScheduler,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import evaluate_execution_readiness
from custom_components.frakon_energy.load_execution_start_scheduler import (
    STATUS_DISARMED,
    STATUS_STARTED_VERIFIED,
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
    STATUS_VERIFIED as STOP_STATUS_VERIFIED,
    ExecutionStopScheduler,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_GENERIC, LoadProfile

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 16, 0, tzinfo=TZ)
END = START + timedelta(hours=1)


class _Store:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = data


class _CancellationRepo:
    async def async_get_by_attempt_id(self, attempt_id: str):
        return None


class _States:
    def __init__(self) -> None:
        self.value = "off"

    def get(self, entity_id: str) -> object:
        return SimpleNamespace(state=self.value)


class _Services:
    def __init__(self, states: _States) -> None:
        self.states = states
        self.calls: list[dict[str, Any]] = []

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
        elif service == "turn_off":
            self.states.value = "off"
        else:
            raise AssertionError(f"unexpected physical service: {domain}.{service}")


class _ArmGuard:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Hass:
    def __init__(self, *, armed: bool) -> None:
        self.data: dict[str, Any] = {}
        self.states = _States()
        self.services = _Services(self.states)
        self.tasks: list[asyncio.Task[Any]] = []
        self.execution_armed = armed

    def async_create_task(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task


def _profile() -> LoadProfile:
    return LoadProfile(
        "commissioning-helper",
        "Commissioning helper",
        PROFILE_KIND_GENERIC,
        60,
        0.1,
        entity_id="input_boolean.frakon_execution_test",
    )


def _policy() -> LoadExecutionPolicy:
    return LoadExecutionPolicy(
        "commissioning-helper",
        mode=EXECUTION_MODE_APPROVAL_REQUIRED,
        max_power_kw=0.1,
        max_duration_minutes=60,
    )


def _plan() -> LoadPlan:
    return LoadPlan(
        load_id="commissioning-helper",
        name="Commissioning helper",
        starts_at=START.isoformat(),
        ends_at=END.isoformat(),
        duration_minutes=60,
        interval_count=4,
        power_kw=0.1,
        average_czk_kwh=2.0,
        minimum_czk_kwh=1.5,
        maximum_czk_kwh=2.5,
        estimated_energy_kwh=0.1,
        estimated_cost_czk=0.2,
    )


def _artifacts() -> tuple[
    ExecutionAttempt,
    ExecutionActionSnapshot,
    ExecutionLifecycleRecord,
    ExecutionPendingRun,
    ExecutionStopLease,
]:
    profile = _profile()
    policy = _policy()
    plan = _plan()
    attempt = ExecutionAttempt(
        attempt_id="attempt-commissioning",
        entry_id="entry-1",
        profile_id=profile.profile_id,
        entity_id=profile.entity_id,
        approval_id="approval-commissioning",
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
    prepared = ExecutionLifecycleRecord.prepared(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=plan,
        readiness=readiness,
        created_at=int(START.timestamp()),
    )
    pending = ExecutionPendingRun.from_records(
        attempt=attempt,
        action_snapshot=snapshot,
        plan=ExecutionPlanSnapshot.from_load_plan(plan),
        created_at=int((START - timedelta(minutes=1)).timestamp()),
    )
    lease = ExecutionStopLease.from_prepared_lifecycle(
        prepared,
        created_at=prepared.updated_at,
    )
    return attempt, snapshot, prepared, pending, lease


def _bounded_gate_payload(
    start: ExecutionLifecycleRecord,
    lease: ExecutionStopLease,
) -> dict[str, Any]:
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


async def _repositories(pending: ExecutionPendingRun):
    pending_repo = ExecutionPendingRunRepository(_Store())
    await pending_repo.async_record(pending)
    start_repo = ExecutionLifecycleRepository(_Store())
    lease_repo = ExecutionStopLeaseRepository(_Store())
    stop_repo = ExecutionStopLifecycleRepository(_Store())
    return pending_repo, start_repo, lease_repo, stop_repo


def _wire_physical_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    hass: _Hass,
    *,
    expected_lease: ExecutionStopLease,
    start_repo: ExecutionLifecycleRepository,
    lease_repo: ExecutionStopLeaseRepository,
    stop_repo: ExecutionStopLifecycleRepository,
    start_runtime: ExecutionStartScheduler,
    stop_runtime: ExecutionStopScheduler,
    stop_timers: list[tuple[Any, datetime]],
) -> None:
    monkeypatch.setattr(start_scheduler_mod, "lifecycle_repository", lambda hass, entry_id: start_repo)
    monkeypatch.setattr(start_dispatcher, "lifecycle_repository", lambda hass, entry_id: start_repo)
    monkeypatch.setattr(recovery_verification, "lifecycle_repository", lambda hass, entry_id: start_repo)
    monkeypatch.setattr(start_dispatcher, "stop_lifecycle_repository", lambda hass, entry_id: stop_repo)
    monkeypatch.setattr(stop_scheduler_mod, "stop_lifecycle_repository", lambda hass, entry_id: stop_repo)
    monkeypatch.setattr(stop_dispatcher, "stop_lifecycle_repository", lambda hass, entry_id: stop_repo)
    monkeypatch.setattr(stop_resolution, "stop_lifecycle_repository", lambda hass, entry_id: stop_repo)
    monkeypatch.setattr(ownership_mod, "stop_lifecycle_repository", lambda hass, entry_id: stop_repo)
    monkeypatch.setattr(ownership_mod, "stop_lease_repository", lambda hass, entry_id: lease_repo)

    ok = SimpleNamespace(status="ok")
    monkeypatch.setattr(start_scheduler_mod, "lifecycle_recovery_summary", lambda hass, entry_id: ok)
    monkeypatch.setattr(start_scheduler_mod, "stop_recovery_summary", lambda hass, entry_id: ok)
    monkeypatch.setattr(start_scheduler_mod, "stop_scheduler", lambda hass, entry_id: stop_runtime)
    monkeypatch.setattr(start_dispatcher, "assert_lifecycle_recovery_ready", lambda hass, entry_id: None)
    monkeypatch.setattr(start_dispatcher, "assert_stop_recovery_ready", lambda hass, entry_id: None)
    monkeypatch.setattr(start_dispatcher, "stop_scheduler", lambda hass, entry_id: stop_runtime)
    monkeypatch.setattr(recovery_verification, "assert_lifecycle_recovery_ready", lambda hass, entry_id: None)
    monkeypatch.setattr(stop_dispatcher, "assert_stop_recovery_ready", lambda hass, entry_id: None)
    monkeypatch.setattr(stop_resolution, "assert_stop_recovery_ready", lambda hass, entry_id: None)
    monkeypatch.setattr(stop_scheduler_mod, "stop_recovery_summary", lambda hass, entry_id: ok)

    async def arm_status(hass_obj, entry_id):
        return {
            "armed": hass.execution_armed,
            "storage_healthy": True,
            "last_error": None,
        }

    async def require_armed(hass_obj, entry_id):
        if not hass.execution_armed:
            raise start_dispatcher.ExecutionDisarmedError(
                "physical start execution is DISARMED"
            )
        return SimpleNamespace(armed=True)

    monkeypatch.setattr(start_scheduler_mod, "async_execution_arm_status", arm_status)
    monkeypatch.setattr(start_dispatcher, "async_require_execution_armed", require_armed)
    monkeypatch.setattr(
        start_dispatcher,
        "execution_arm_guard",
        lambda hass, entry_id: _ArmGuard(),
    )

    async def bounded_gate(hass_obj, *, entry_id, attempt_id, now):
        current = await start_repo.async_get_by_attempt_id(attempt_id)
        assert current is not None
        lease = await lease_repo.async_get_by_lifecycle_id(current.lifecycle_id)
        assert lease is not None and lease == expected_lease
        return _bounded_gate_payload(current, lease)

    monkeypatch.setattr(start_scheduler_mod, "async_bounded_dispatch_gate", bounded_gate)
    monkeypatch.setattr(start_dispatcher, "async_bounded_dispatch_gate", bounded_gate)

    async def refresh_stop(hass_obj, entry_id):
        if stop_runtime.started:
            await stop_runtime.async_refresh(now=START)

    monkeypatch.setattr(
        start_dispatcher,
        "async_refresh_stop_scheduler_if_started",
        refresh_stop,
    )

    async def no_stop_refresh(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        stop_dispatcher,
        "async_refresh_stop_scheduler_if_started",
        no_stop_refresh,
    )

    def track_stop(hass_obj, action, when):
        stop_timers.append((action, when))
        return lambda: None

    monkeypatch.setattr(
        stop_scheduler_mod,
        "async_track_point_in_utc_time",
        track_stop,
    )


def _wire_pending_scheduler(
    monkeypatch: pytest.MonkeyPatch,
    hass: _Hass,
    *,
    pending_repo: ExecutionPendingRunRepository,
    start_repo: ExecutionLifecycleRepository,
    lease_repo: ExecutionStopLeaseRepository,
    expected_prepared: ExecutionLifecycleRecord,
    expected_lease: ExecutionStopLease,
    start_runtime: ExecutionStartScheduler,
    stop_runtime: ExecutionStopScheduler,
    pending_timers: list[tuple[Any, datetime]],
) -> None:
    monkeypatch.setattr(
        pending_scheduler_mod,
        "pending_run_repository",
        lambda hass, entry_id: pending_repo,
    )
    monkeypatch.setattr(
        pending_scheduler_mod,
        "lifecycle_repository",
        lambda hass, entry_id: start_repo,
    )
    monkeypatch.setattr(
        pending_scheduler_mod,
        "cancellation_repository",
        lambda hass, entry_id: _CancellationRepo(),
    )
    ok = SimpleNamespace(status="ok")
    monkeypatch.setattr(
        pending_scheduler_mod,
        "lifecycle_recovery_summary",
        lambda hass, entry_id: ok,
    )
    monkeypatch.setattr(
        pending_scheduler_mod,
        "stop_recovery_summary",
        lambda hass, entry_id: ok,
    )
    monkeypatch.setattr(
        pending_scheduler_mod,
        "start_scheduler",
        lambda hass, entry_id: start_runtime,
    )
    monkeypatch.setattr(
        pending_scheduler_mod,
        "stop_scheduler",
        lambda hass, entry_id: stop_runtime,
    )

    async def prepare_lifecycle(
        hass_obj,
        *,
        entry_id,
        attempt_id,
        plan_value,
        now,
    ):
        assert attempt_id == expected_prepared.attempt_id
        assert plan_value == expected_prepared.plan.as_dict()
        result = await start_repo.async_prepare(expected_prepared)
        return {
            "lifecycle": result.record.as_dict(),
            "prepared_only": True,
            "service_call_performed": False,
            "execution_performed": False,
        }

    async def prepare_stop_lease(hass_obj, *, entry_id, attempt_id, now):
        current = await start_repo.async_get_by_attempt_id(attempt_id)
        assert current is not None and current.state == STATE_PREPARED
        result = await lease_repo.async_record(expected_lease)
        await start_runtime.async_refresh(now=now)
        return {
            "stop_lease": result.lease.as_dict(),
            "stop_obligation_armed": True,
            "service_call_performed": False,
            "execution_performed": False,
        }

    monkeypatch.setattr(
        pending_scheduler_mod,
        "async_prepare_execution_lifecycle",
        prepare_lifecycle,
    )
    monkeypatch.setattr(
        pending_scheduler_mod,
        "async_prepare_stop_lease",
        prepare_stop_lease,
    )

    def track_pending(hass_obj, action, when):
        pending_timers.append((action, when))
        return lambda: None

    monkeypatch.setattr(
        pending_scheduler_mod,
        "async_track_point_in_utc_time",
        track_pending,
    )


def _services(hass: _Hass) -> list[str]:
    return [str(call["service"]) for call in hass.services.calls]


async def _runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    armed: bool,
):
    _, _, prepared, pending, lease = _artifacts()
    pending_repo, start_repo, lease_repo, stop_repo = await _repositories(pending)
    hass = _Hass(armed=armed)
    pending_timers: list[tuple[Any, datetime]] = []
    stop_timers: list[tuple[Any, datetime]] = []
    stop_runtime = ExecutionStopScheduler(hass, "entry-1")  # type: ignore[arg-type]
    stop_runtime._started = True
    start_runtime = ExecutionStartScheduler(hass, "entry-1")  # type: ignore[arg-type]
    start_runtime._started = True
    _wire_physical_pipeline(
        monkeypatch,
        hass,
        expected_lease=lease,
        start_repo=start_repo,
        lease_repo=lease_repo,
        stop_repo=stop_repo,
        start_runtime=start_runtime,
        stop_runtime=stop_runtime,
        stop_timers=stop_timers,
    )
    _wire_pending_scheduler(
        monkeypatch,
        hass,
        pending_repo=pending_repo,
        start_repo=start_repo,
        lease_repo=lease_repo,
        expected_prepared=prepared,
        expected_lease=lease,
        start_runtime=start_runtime,
        stop_runtime=stop_runtime,
        pending_timers=pending_timers,
    )
    pending_runtime = ExecutionPendingRunScheduler(hass, "entry-1")  # type: ignore[arg-type]
    pending_runtime._started = True
    return (
        hass,
        prepared,
        start_repo,
        lease_repo,
        stop_repo,
        pending_runtime,
        start_runtime,
        stop_runtime,
        pending_timers,
        stop_timers,
    )


@pytest.mark.asyncio
async def test_pending_timer_armed_runs_exactly_once_then_stops_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        hass,
        prepared,
        start_repo,
        lease_repo,
        stop_repo,
        pending_runtime,
        start_runtime,
        stop_runtime,
        pending_timers,
        stop_timers,
    ) = await _runtime(monkeypatch, armed=True)

    await pending_runtime.async_refresh(now=START - timedelta(minutes=1))
    assert len(pending_timers) == 1
    assert pending_timers[0][1] == START.astimezone(timezone.utc)
    assert _services(hass) == []

    pending_timers[0][0](START)
    await asyncio.gather(*hass.tasks)

    assert _services(hass) == ["turn_on"]
    assert hass.states.value == "on"
    current = await start_repo.async_get_by_attempt_id(prepared.attempt_id)
    assert current is not None and current.state == STATE_VERIFIED
    persisted_lease = await lease_repo.async_get_by_lifecycle_id(prepared.lifecycle_id)
    assert persisted_lease is not None
    stop = await stop_repo.async_get_by_start_lifecycle_id(prepared.lifecycle_id)
    assert stop is not None and stop.state == STOP_STATE_OWNED
    assert pending_runtime.statuses()[0].status == STATUS_DELEGATED
    assert start_runtime.statuses()[0].status == STATUS_STARTED_VERIFIED
    assert stop_timers and stop_timers[-1][1] == END.astimezone(timezone.utc)

    stop_timers[-1][0](END)
    await asyncio.gather(*hass.tasks)

    assert _services(hass) == ["turn_on", "turn_off"]
    assert hass.states.value == "off"
    final_stop = await stop_repo.async_get_by_start_lifecycle_id(prepared.lifecycle_id)
    assert final_stop is not None and final_stop.state == STOP_STATE_VERIFIED
    assert stop_runtime.statuses()[0].status == STOP_STATUS_VERIFIED
    assert all(call["service_data"] == {} for call in hass.services.calls)
    assert all(call["blocking"] is True for call in hass.services.calls)
    assert all(call["target"] == {"entity_id": prepared.entity_id} for call in hass.services.calls)


@pytest.mark.asyncio
async def test_pending_timer_disarmed_prepares_stop_lease_without_call_then_arm_runs_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        hass,
        prepared,
        start_repo,
        lease_repo,
        stop_repo,
        pending_runtime,
        start_runtime,
        stop_runtime,
        pending_timers,
        stop_timers,
    ) = await _runtime(monkeypatch, armed=False)

    await pending_runtime.async_refresh(now=START - timedelta(minutes=1))
    pending_timers[0][0](START)
    await asyncio.gather(*hass.tasks)

    assert _services(hass) == []
    assert hass.states.value == "off"
    current = await start_repo.async_get_by_attempt_id(prepared.attempt_id)
    assert current is not None and current.state == STATE_PREPARED
    persisted_lease = await lease_repo.async_get_by_lifecycle_id(prepared.lifecycle_id)
    assert persisted_lease is not None
    assert await stop_repo.async_get_by_start_lifecycle_id(prepared.lifecycle_id) is None
    assert pending_runtime.statuses()[0].status == STATUS_PREPARED_WITH_STOP_LEASE
    assert start_runtime.statuses()[0].status == STATUS_DISARMED
    assert stop_timers == []

    hass.execution_armed = True
    await start_runtime.async_refresh(now=START + timedelta(seconds=1))

    assert _services(hass) == ["turn_on"]
    assert hass.states.value == "on"
    current = await start_repo.async_get_by_attempt_id(prepared.attempt_id)
    assert current is not None and current.state == STATE_VERIFIED
    stop = await stop_repo.async_get_by_start_lifecycle_id(prepared.lifecycle_id)
    assert stop is not None and stop.state == STOP_STATE_OWNED
    assert stop_timers and stop_timers[-1][1] == END.astimezone(timezone.utc)

    hass.execution_armed = False
    stop_timers[-1][0](END)
    await asyncio.gather(*hass.tasks)

    assert _services(hass) == ["turn_on", "turn_off"]
    assert hass.states.value == "off"
    final_stop = await stop_repo.async_get_by_start_lifecycle_id(prepared.lifecycle_id)
    assert final_stop is not None and final_stop.state == STOP_STATE_VERIFIED
    assert stop_runtime.statuses()[0].status == STOP_STATUS_VERIFIED
