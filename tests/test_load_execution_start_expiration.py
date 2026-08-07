from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_start_expiration as expiration
from custom_components.frakon_energy import load_execution_start_scheduler as scheduler_mod
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_action_intent import resolve_start_action_intent
from custom_components.frakon_energy.load_execution_action_snapshot import ExecutionActionSnapshot
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt
from custom_components.frakon_energy.load_execution_bounded_dispatch_gate import BOUNDED_GATE_BLOCKED
from custom_components.frakon_energy.load_execution_lifecycle import (
    CALL_NOT_STARTED,
    STATE_FAILED,
    STATE_PREPARED,
    ExecutionLifecycleRecord,
    ExecutionLifecycleRepository,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_readiness import (
    REASON_PLAN_EXPIRED,
    REASON_START_MISSED,
    evaluate_execution_readiness,
)
from custom_components.frakon_energy.load_execution_start_scheduler import (
    STATUS_EXPIRED,
    STATUS_WAITING_STOP_LEASE,
    ExecutionStartScheduler,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)


class _Store:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.saves = 0

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = data
        self.saves += 1


class _Hass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}


class _StopRepo:
    def __init__(self, stop: object | None = None) -> None:
        self.stop = stop

    async def async_get_by_start_lifecycle_id(self, lifecycle_id: str):
        return self.stop


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


async def _repo() -> tuple[_Store, ExecutionLifecycleRepository]:
    store = _Store()
    repo = ExecutionLifecycleRepository(store)
    await repo.async_prepare(_prepared())
    return store, repo


def _gate(reason: str) -> dict[str, Any]:
    return {
        "bounded_dispatch_gate": {
            "status": BOUNDED_GATE_BLOCKED,
            "reason": reason,
        }
    }


def _wire_expiration(
    monkeypatch: pytest.MonkeyPatch,
    repo: ExecutionLifecycleRepository,
    *,
    reason: str,
    stop: object | None = None,
) -> None:
    monkeypatch.setattr(expiration, "lifecycle_repository", lambda hass, entry_id: repo)
    monkeypatch.setattr(expiration, "stop_lifecycle_repository", lambda hass, entry_id: _StopRepo(stop))
    monkeypatch.setattr(expiration, "assert_lifecycle_recovery_ready", lambda hass, entry_id: None)

    async def gate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return _gate(reason)

    monkeypatch.setattr(expiration, "async_bounded_dispatch_gate", gate)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [REASON_START_MISSED, REASON_PLAN_EXPIRED])
async def test_irreversibly_missed_prepared_start_expires_without_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
) -> None:
    store, repo = await _repo()
    _wire_expiration(monkeypatch, repo, reason=reason)
    before_saves = store.saves

    result = await expiration.async_expire_prepared_start(
        _Hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START + timedelta(hours=3),
    )

    record = await repo.async_get_by_attempt_id("attempt-1")
    assert record is not None
    assert record.state == STATE_FAILED
    assert record.service_call_status == CALL_NOT_STARTED
    assert record.failure_reason == f"{expiration.EXPIRATION_PREFIX}{reason}"
    assert result["expiration_performed"] is True
    assert result["service_call_performed"] is False
    assert result["execution_performed"] is False
    assert store.saves == before_saves + 1


@pytest.mark.asyncio
async def test_expiration_retry_is_idempotent_without_store_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo = await _repo()
    _wire_expiration(monkeypatch, repo, reason=REASON_START_MISSED)
    hass = _Hass()
    first = await expiration.async_expire_prepared_start(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START + timedelta(hours=3),
    )
    before_saves = store.saves
    retry = await expiration.async_expire_prepared_start(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=START + timedelta(hours=4),
    )

    assert first["lifecycle"] == retry["lifecycle"]
    assert retry["idempotent_replay"] is True
    assert retry["expiration_performed"] is False
    assert store.saves == before_saves


@pytest.mark.asyncio
async def test_recoverable_block_is_not_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo = await _repo()
    _wire_expiration(monkeypatch, repo, reason="matching_stop_lease_required")
    before_saves = store.saves

    with pytest.raises(expiration.StartExpirationError, match="not terminally expired"):
        await expiration.async_expire_prepared_start(
            _Hass(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START + timedelta(minutes=1),
        )

    record = await repo.async_get_by_attempt_id("attempt-1")
    assert record is not None and record.state == STATE_PREPARED
    assert store.saves == before_saves


@pytest.mark.asyncio
async def test_prepared_start_with_stop_ownership_is_never_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, repo = await _repo()
    _wire_expiration(
        monkeypatch,
        repo,
        reason=REASON_PLAN_EXPIRED,
        stop=SimpleNamespace(state="owned"),
    )

    with pytest.raises(expiration.StartExpirationError, match="durable stop ownership"):
        await expiration.async_expire_prepared_start(
            _Hass(),  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id="attempt-1",
            now=START + timedelta(hours=3),
        )


@pytest.mark.asyncio
async def test_scheduler_expires_once_and_never_dispatches_stale_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, repo = await _repo()
    hass = _Hass()
    _wire_expiration(monkeypatch, repo, reason=REASON_START_MISSED)
    monkeypatch.setattr(scheduler_mod, "lifecycle_repository", lambda hass, entry_id: repo)
    ok = SimpleNamespace(status="ok")
    monkeypatch.setattr(scheduler_mod, "lifecycle_recovery_summary", lambda hass, entry_id: ok)
    monkeypatch.setattr(scheduler_mod, "stop_recovery_summary", lambda hass, entry_id: ok)
    monkeypatch.setattr(
        scheduler_mod,
        "stop_scheduler",
        lambda hass, entry_id: SimpleNamespace(started=True, healthy=True),
    )

    async def gate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return _gate(REASON_START_MISSED)

    dispatch_calls: list[int] = []

    async def dispatch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        dispatch_calls.append(1)
        return {}

    monkeypatch.setattr(scheduler_mod, "async_bounded_dispatch_gate", gate)
    monkeypatch.setattr(scheduler_mod, "async_dispatch_bounded_start", dispatch)
    monkeypatch.setattr(
        scheduler_mod,
        "async_expire_prepared_start",
        expiration.async_expire_prepared_start,
    )

    scheduler = ExecutionStartScheduler(hass, "entry-1")  # type: ignore[arg-type]
    scheduler._started = True
    await scheduler.async_refresh(now=START + timedelta(hours=3))

    record = await repo.async_get_by_attempt_id("attempt-1")
    assert record is not None and expiration.is_expiration_terminal(record)
    assert scheduler.statuses()[0].status == STATUS_EXPIRED
    assert dispatch_calls == []

    await scheduler.async_refresh(now=START + timedelta(hours=4))

    assert scheduler.statuses()[0].status == STATUS_EXPIRED
    assert dispatch_calls == []


@pytest.mark.asyncio
async def test_scheduler_keeps_missing_stop_lease_recoverable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, repo = await _repo()
    hass = _Hass()
    monkeypatch.setattr(scheduler_mod, "lifecycle_repository", lambda hass, entry_id: repo)
    ok = SimpleNamespace(status="ok")
    monkeypatch.setattr(scheduler_mod, "lifecycle_recovery_summary", lambda hass, entry_id: ok)
    monkeypatch.setattr(scheduler_mod, "stop_recovery_summary", lambda hass, entry_id: ok)
    monkeypatch.setattr(
        scheduler_mod,
        "stop_scheduler",
        lambda hass, entry_id: SimpleNamespace(started=True, healthy=True),
    )

    async def gate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return _gate("matching_stop_lease_required")

    monkeypatch.setattr(scheduler_mod, "async_bounded_dispatch_gate", gate)
    scheduler = ExecutionStartScheduler(hass, "entry-1")  # type: ignore[arg-type]
    scheduler._started = True
    await scheduler.async_refresh(now=START)

    record = await repo.async_get_by_attempt_id("attempt-1")
    assert record is not None and record.state == STATE_PREPARED
    assert scheduler.statuses()[0].status == STATUS_WAITING_STOP_LEASE
