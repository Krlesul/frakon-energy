from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_start_dispatcher as dispatcher
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_bounded_dispatch_gate import (
    BOUNDED_GATE_ALREADY_SATISFIED,
    BOUNDED_GATE_READY,
    BoundedDispatchDecision,
)
from custom_components.frakon_energy.load_execution_lifecycle import (
    CALL_NOT_STARTED,
    CALL_UNKNOWN,
    STATE_DISPATCHED,
    STATE_FAILED,
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
from custom_components.frakon_energy.load_execution_start_stop_ownership import StartStopOwnershipProof
from custom_components.frakon_energy.load_execution_stop_lease import ExecutionStopLease
from custom_components.frakon_energy.load_execution_stop_lifecycle import (
    STOP_STATE_FAILED,
    STOP_STATE_OWNED,
    ExecutionStopLifecycleRepository,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)


class _FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.saves = 0
        self.fail_on_saves: set[int] = set()

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        next_save = self.saves + 1
        self.saves += 1
        if next_save in self.fail_on_saves:
            raise RuntimeError(f"storage unavailable on save {next_save}")
        self.data = data


class _States:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def get(self, entity_id: str) -> object | None:
        return SimpleNamespace(state=self.value) if self.value is not None else None


class _Services:
    def __init__(self, states: _States, flags: dict[str, Any]) -> None:
        self.states = states
        self.flags = flags
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None
        self.set_state_after_call: str | None = "on"

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
        assert self.flags.get("stop_owned") is True
        assert self.flags.get("scheduler_refreshed_before_call", 0) >= 1
        assert self.flags.get("ownership_proved") is True
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
        if self.error is not None:
            raise self.error
        if self.set_state_after_call is not None:
            self.states.value = self.set_state_after_call


class _Hass:
    def __init__(self, state: str | None = "off") -> None:
        self.data: dict[str, Any] = {}
        self.states = _States(state)
        self.flags: dict[str, Any] = {}
        self.services = _Services(self.states, self.flags)


class _Scheduler:
    def __init__(self) -> None:
        self.started = True
        self.healthy = True
        self.last_error: str | None = None


class _ArmGuard:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


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


def _lease() -> ExecutionStopLease:
    prepared = _prepared()
    return ExecutionStopLease.from_prepared_lifecycle(
        prepared,
        created_at=prepared.updated_at,
    )


def _gate_payload(*, status: str = BOUNDED_GATE_READY) -> dict[str, Any]:
    start = _prepared()
    lease = _lease()
    ready = status == BOUNDED_GATE_READY
    decision = BoundedDispatchDecision(
        status=status,
        reason=(
            "bounded_start_has_armed_stop_obligation"
            if ready
            else "desired_state_already_observed"
        ),
        lifecycle_id=start.lifecycle_id,
        attempt_id=start.attempt_id,
        entity_id=start.entity_id,
        start_service_domain=start.service_domain,
        start_service_name=start.service_name,
        stop_lease_id=lease.lease_id if ready else None,
        stop_intent_id=lease.stop_intent_id if ready else None,
        stop_service_domain=lease.service_domain if ready else None,
        stop_service_name=lease.service_name if ready else None,
        stop_at=start.plan.ends_at,
        dispatch_gate_status=("ready_to_dispatch" if ready else "already_satisfied"),
        dispatch_gate_matches=True,
        stop_lease_matches=ready,
        can_start=ready,
    )
    return {
        "lifecycle": start.as_dict(),
        "stop_lease": lease.as_dict() if ready else None,
        "bounded_dispatch_gate": decision.as_dict(),
    }


def _proof(ready: bool = True) -> StartStopOwnershipProof:
    return StartStopOwnershipProof(
        start_lifecycle_id=_prepared().lifecycle_id,
        stop_lease_present=ready,
        stop_lifecycle_present=ready,
        stop_lease_matches=ready,
        stop_lifecycle_matches=ready,
        ownership_ready=ready,
        reason="stop_ownership_ready" if ready else "stop_lifecycle_missing",
    )


async def _repositories():
    start_store = _FakeStore()
    start_repo = ExecutionLifecycleRepository(start_store)
    await start_repo.async_prepare(_prepared())
    stop_store = _FakeStore()
    stop_repo = ExecutionStopLifecycleRepository(stop_store)
    return start_store, start_repo, stop_store, stop_repo


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    hass: _Hass,
    start_repo: ExecutionLifecycleRepository,
    stop_repo: ExecutionStopLifecycleRepository,
    scheduler: _Scheduler,
    *,
    ownership_ready: bool = True,
    unhealthy_after_refresh: bool = False,
    gate_status: str = BOUNDED_GATE_READY,
) -> None:
    monkeypatch.setattr(dispatcher, "lifecycle_repository", lambda hass, entry_id: start_repo)
    monkeypatch.setattr(dispatcher, "stop_lifecycle_repository", lambda hass, entry_id: stop_repo)
    monkeypatch.setattr(dispatcher, "assert_lifecycle_recovery_ready", lambda hass, entry_id: None)
    monkeypatch.setattr(dispatcher, "assert_stop_recovery_ready", lambda hass, entry_id: None)
    monkeypatch.setattr(dispatcher, "stop_scheduler", lambda hass, entry_id: scheduler)
    monkeypatch.setattr(dispatcher, "execution_arm_guard", lambda hass, entry_id: _ArmGuard())

    async def require_armed(hass_obj, entry_id):
        return SimpleNamespace(armed=True)

    monkeypatch.setattr(dispatcher, "async_require_execution_armed", require_armed)

    async def bounded_gate(hass, *, entry_id, attempt_id, now):
        return _gate_payload(status=gate_status)

    monkeypatch.setattr(dispatcher, "async_bounded_dispatch_gate", bounded_gate)

    async def refresh(hass_obj, entry_id):
        hass.flags["scheduler_refreshed_before_call"] = (
            hass.flags.get("scheduler_refreshed_before_call", 0) + 1
        )
        current_stop = await stop_repo.async_get_by_start_lifecycle_id(_prepared().lifecycle_id)
        if current_stop is not None and current_stop.state == STOP_STATE_OWNED:
            hass.flags["stop_owned"] = True
        if unhealthy_after_refresh:
            scheduler.healthy = False
            scheduler.last_error = "timer registration failed"

    monkeypatch.setattr(dispatcher, "async_refresh_stop_scheduler_if_started", refresh)

    async def ownership(hass_obj, *, entry_id, start):
        hass.flags["ownership_proved"] = ownership_ready
        return _proof(ownership_ready)

    monkeypatch.setattr(dispatcher, "async_start_stop_ownership_proof", ownership)


@pytest.mark.asyncio
async def test_success_persists_stop_ownership_before_exact_immutable_turn_on_and_verifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_store, start_repo, _, stop_repo = await _repositories()
    hass = _Hass("off")
    scheduler = _Scheduler()
    _wire(monkeypatch, hass, start_repo, stop_repo, scheduler)
    context = object()

    result = await dispatcher.async_dispatch_bounded_start(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        context=context,  # type: ignore[arg-type]
        now=START,
    )

    assert hass.services.calls == [
        {
            "domain": "switch",
            "service": "turn_on",
            "service_data": {},
            "blocking": True,
            "context": context,
            "target": {"entity_id": "switch.enyaq_charging"},
        }
    ]
    start = await start_repo.async_get_by_attempt_id("attempt-1")
    stop = await stop_repo.async_get_by_start_lifecycle_id(_prepared().lifecycle_id)
    assert start is not None and start.state == STATE_VERIFIED
    assert stop is not None and stop.state == STOP_STATE_OWNED
    assert result["status"] == "start_verified"
    assert result["stop_ownership"]["ownership_ready"] is True
    assert result["service_call_performed"] is True
    assert result["execution_performed"] is True
    assert result["can_redispatch"] is False
    # prepare + dispatching + confirmed + verified
    assert start_store.saves == 4


@pytest.mark.asyncio
async def test_disarmed_before_start_transition_blocks_without_stop_ownership_or_turn_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, start_repo, _, stop_repo = await _repositories()
    hass = _Hass("off")
    _wire(monkeypatch, hass, start_repo, stop_repo, _Scheduler())

    async def disarmed(hass_obj, entry_id):
        raise dispatcher.ExecutionDisarmedError("physical start execution is DISARMED")

    monkeypatch.setattr(dispatcher, "async_require_execution_armed", disarmed)

    with pytest.raises(dispatcher.StartDispatchError, match="DISARMED"):
        await dispatcher.async_dispatch_bounded_start(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START,
        )

    assert hass.services.calls == []
    assert (await start_repo.async_get_by_attempt_id("attempt-1")) == _prepared()
    assert await stop_repo.async_list() == ()


@pytest.mark.asyncio
async def test_disarm_after_stop_ownership_aborts_both_before_turn_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, start_repo, _, stop_repo = await _repositories()
    hass = _Hass("off")
    _wire(monkeypatch, hass, start_repo, stop_repo, _Scheduler())
    checks = 0

    async def armed_then_disarmed(hass_obj, entry_id):
        nonlocal checks
        checks += 1
        if checks == 1:
            return SimpleNamespace(armed=True)
        raise dispatcher.ExecutionDisarmedError("physical start execution is DISARMED")

    monkeypatch.setattr(dispatcher, "async_require_execution_armed", armed_then_disarmed)

    with pytest.raises(dispatcher.StartDispatchError, match="execution interlock"):
        await dispatcher.async_dispatch_bounded_start(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START,
        )

    assert checks == 2
    assert hass.services.calls == []
    start = await start_repo.async_get_by_attempt_id("attempt-1")
    stop = await stop_repo.async_get_by_start_lifecycle_id(_prepared().lifecycle_id)
    assert start is not None and start.state == STATE_FAILED
    assert start.service_call_status == CALL_NOT_STARTED
    assert stop is not None and stop.state == STOP_STATE_FAILED


@pytest.mark.asyncio
async def test_start_dispatching_store_failure_prevents_stop_ownership_and_turn_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_store, start_repo, _, stop_repo = await _repositories()
    start_store.fail_on_saves.add(2)
    hass = _Hass("off")
    _wire(monkeypatch, hass, start_repo, stop_repo, _Scheduler())

    with pytest.raises(RuntimeError, match="storage unavailable on save 2"):
        await dispatcher.async_dispatch_bounded_start(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START,
        )

    assert hass.services.calls == []
    assert await stop_repo.async_list() == ()
    assert (await start_repo.async_get_by_attempt_id("attempt-1")) == _prepared()


@pytest.mark.asyncio
async def test_stop_ownership_persistence_failure_aborts_start_without_turn_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, start_repo, stop_store, stop_repo = await _repositories()
    stop_store.fail_on_saves.add(1)
    hass = _Hass("off")
    _wire(monkeypatch, hass, start_repo, stop_repo, _Scheduler())

    with pytest.raises(dispatcher.StartDispatchError, match="could not be persisted"):
        await dispatcher.async_dispatch_bounded_start(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START,
        )

    assert hass.services.calls == []
    start = await start_repo.async_get_by_attempt_id("attempt-1")
    assert start is not None and start.state == STATE_FAILED
    assert start.service_call_status == CALL_NOT_STARTED
    assert await stop_repo.async_list() == ()


@pytest.mark.asyncio
async def test_scheduler_becoming_unhealthy_after_ownership_fails_both_before_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, start_repo, _, stop_repo = await _repositories()
    hass = _Hass("off")
    scheduler = _Scheduler()
    _wire(
        monkeypatch,
        hass,
        start_repo,
        stop_repo,
        scheduler,
        unhealthy_after_refresh=True,
    )

    with pytest.raises(dispatcher.StartDispatchError, match="scheduler became unhealthy"):
        await dispatcher.async_dispatch_bounded_start(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START,
        )

    assert hass.services.calls == []
    start = await start_repo.async_get_by_attempt_id("attempt-1")
    stop = await stop_repo.async_get_by_start_lifecycle_id(_prepared().lifecycle_id)
    assert start is not None and start.state == STATE_FAILED
    assert start.service_call_status == CALL_NOT_STARTED
    assert stop is not None and stop.state == STOP_STATE_FAILED


@pytest.mark.asyncio
async def test_cross_store_ownership_proof_failure_aborts_both_before_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, start_repo, _, stop_repo = await _repositories()
    hass = _Hass("off")
    _wire(
        monkeypatch,
        hass,
        start_repo,
        stop_repo,
        _Scheduler(),
        ownership_ready=False,
    )

    with pytest.raises(dispatcher.StartDispatchError, match="ownership proof failed"):
        await dispatcher.async_dispatch_bounded_start(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START,
        )

    assert hass.services.calls == []
    start = await start_repo.async_get_by_attempt_id("attempt-1")
    stop = await stop_repo.async_get_by_start_lifecycle_id(_prepared().lifecycle_id)
    assert start is not None and start.state == STATE_FAILED
    assert stop is not None and stop.state == STOP_STATE_FAILED


@pytest.mark.asyncio
async def test_service_exception_becomes_unknown_recovery_with_stop_owned_and_no_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, start_repo, _, stop_repo = await _repositories()
    hass = _Hass("off")
    hass.services.error = RuntimeError("transport failed")
    _wire(monkeypatch, hass, start_repo, stop_repo, _Scheduler())

    with pytest.raises(dispatcher.StartDispatchUnknownOutcomeError, match="outcome is unknown"):
        await dispatcher.async_dispatch_bounded_start(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START,
        )

    assert len(hass.services.calls) == 1
    start = await start_repo.async_get_by_attempt_id("attempt-1")
    stop = await stop_repo.async_get_by_start_lifecycle_id(_prepared().lifecycle_id)
    assert start is not None and start.state == STATE_RECOVERY_REQUIRED
    assert start.service_call_status == CALL_UNKNOWN
    assert stop is not None and stop.state == STOP_STATE_OWNED

    async def reject_verify(*args, **kwargs):
        raise ValueError("desired state not observed")

    monkeypatch.setattr(dispatcher, "async_verify_recovery_lifecycle", reject_verify)
    hass.services.error = None
    with pytest.raises(dispatcher.StartDispatchError, match="cannot be retried"):
        await dispatcher.async_dispatch_bounded_start(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START + timedelta(seconds=1),
        )
    assert len(hass.services.calls) == 1


@pytest.mark.asyncio
async def test_confirm_persistence_failure_converts_start_to_unknown_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_store, start_repo, _, stop_repo = await _repositories()
    # save1 prepare, save2 dispatching, save3 confirmed fails, save4 recovery succeeds.
    start_store.fail_on_saves.add(3)
    hass = _Hass("off")
    _wire(monkeypatch, hass, start_repo, stop_repo, _Scheduler())

    with pytest.raises(dispatcher.StartDispatchUnknownOutcomeError, match="confirmed evidence"):
        await dispatcher.async_dispatch_bounded_start(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START,
        )

    assert len(hass.services.calls) == 1
    start = await start_repo.async_get_by_attempt_id("attempt-1")
    stop = await stop_repo.async_get_by_start_lifecycle_id(_prepared().lifecycle_id)
    assert start is not None and start.state == STATE_RECOVERY_REQUIRED
    assert start.service_call_status == CALL_UNKNOWN
    assert stop is not None and stop.state == STOP_STATE_OWNED


@pytest.mark.asyncio
async def test_normal_turn_on_return_but_entity_still_off_is_pending_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, start_repo, _, stop_repo = await _repositories()
    hass = _Hass("off")
    hass.services.set_state_after_call = "off"
    _wire(monkeypatch, hass, start_repo, stop_repo, _Scheduler())

    result = await dispatcher.async_dispatch_bounded_start(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START,
    )
    assert result["status"] == "start_dispatched_pending_verification"
    assert len(hass.services.calls) == 1
    start = await start_repo.async_get_by_attempt_id("attempt-1")
    assert start is not None and start.state == STATE_DISPATCHED

    async def reject_verify(*args, **kwargs):
        raise ValueError("desired state not observed")

    monkeypatch.setattr(dispatcher, "async_verify_recovery_lifecycle", reject_verify)
    with pytest.raises(dispatcher.StartDispatchError, match="cannot be retried"):
        await dispatcher.async_dispatch_bounded_start(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START + timedelta(seconds=1),
        )
    assert len(hass.services.calls) == 1


@pytest.mark.asyncio
async def test_already_satisfied_routes_to_noop_without_stop_ownership_or_turn_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, start_repo, _, stop_repo = await _repositories()
    hass = _Hass("on")
    _wire(
        monkeypatch,
        hass,
        start_repo,
        stop_repo,
        _Scheduler(),
        gate_status=BOUNDED_GATE_ALREADY_SATISFIED,
    )
    called: list[str] = []

    async def noop(hass_obj, *, entry_id, attempt_id, now):
        called.append(attempt_id)
        return {
            "noop_completed": True,
            "service_call_performed": False,
            "execution_performed": False,
        }

    monkeypatch.setattr(dispatcher, "async_complete_already_satisfied_noop", noop)

    result = await dispatcher.async_dispatch_bounded_start(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START,
    )

    assert called == ["attempt-1"]
    assert hass.services.calls == []
    assert await stop_repo.async_list() == ()
    assert result["status"] == "already_satisfied_no_start"
    assert result["physical_dispatch_attempted"] is False


@pytest.mark.asyncio
async def test_unhealthy_stop_scheduler_blocks_before_any_start_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, start_repo, _, stop_repo = await _repositories()
    scheduler = _Scheduler()
    scheduler.healthy = False
    hass = _Hass("off")
    _wire(monkeypatch, hass, start_repo, stop_repo, scheduler)

    with pytest.raises(dispatcher.StartDispatchError, match="not started and healthy"):
        await dispatcher.async_dispatch_bounded_start(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START,
        )

    assert hass.services.calls == []
    assert (await start_repo.async_get_by_attempt_id("attempt-1")) == _prepared()
    assert await stop_repo.async_list() == ()