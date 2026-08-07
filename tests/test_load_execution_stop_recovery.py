from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_stop_recovery as recovery
from custom_components.frakon_energy.load_execution_stop_lifecycle import (
    STOP_CALL_CONFIRMED,
    STOP_CALL_UNKNOWN,
    STOP_STATE_DISPATCHED,
    STOP_STATE_DISPATCHING,
    STOP_STATE_OWNED,
    STOP_STATE_RECOVERY_REQUIRED,
    ExecutionStopLifecycleRecord,
    ExecutionStopLifecycleRepository,
)

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)
END = START + timedelta(hours=2)


class _FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.fail_save = False
        self.saves = 0

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


def _owned() -> ExecutionStopLifecycleRecord:
    return ExecutionStopLifecycleRecord(
        stop_lifecycle_id="e" * 32,
        lease_id="a" * 32,
        entry_id="entry-1",
        start_lifecycle_id="b" * 32,
        attempt_id="attempt-1",
        action_snapshot_id="c" * 32,
        profile_id="ev-home",
        entity_id="switch.enyaq_charging",
        approval_snapshot_digest="d" * 64,
        plan_digest="f" * 64,
        starts_at=START.isoformat(),
        ends_at=END.isoformat(),
        service_domain="switch",
        service_name="turn_off",
        desired_state="off",
        state=STOP_STATE_OWNED,
        service_call_status="not_started",
        verification_status="pending",
        created_at=int(START.timestamp()),
        updated_at=int(START.timestamp()),
    ).validated()


async def _repository_with(record: ExecutionStopLifecycleRecord):
    store = _FakeStore()
    repository = ExecutionStopLifecycleRepository(store)
    await repository.async_create_owned(_owned())
    if record.state != STOP_STATE_OWNED:
        await repository.async_update(record)
    return store, repository


@pytest.mark.asyncio
async def test_empty_stop_recovery_marks_entry_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = _FakeHass()
    repository = ExecutionStopLifecycleRepository(_FakeStore())
    monkeypatch.setattr(recovery, "stop_lifecycle_repository", lambda hass, entry_id: repository)

    summary = await recovery.async_initialize_stop_recovery(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        now=START,
    )

    assert summary.status == recovery.STOP_RECOVERY_OK
    assert summary.scanned == 0
    assert summary.transitioned_to_recovery == 0
    recovery.assert_stop_recovery_ready(hass, "entry-1")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_interrupted_stop_dispatch_becomes_recovery_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned = _owned()
    dispatching = replace(
        owned,
        state=STOP_STATE_DISPATCHING,
        service_call_status=STOP_CALL_UNKNOWN,
        dispatch_attempts=1,
        dispatch_started_at=int(END.timestamp()),
        updated_at=int(END.timestamp()),
    ).validated()
    store, repository = await _repository_with(dispatching)
    monkeypatch.setattr(recovery, "stop_lifecycle_repository", lambda hass, entry_id: repository)
    hass = _FakeHass()
    before_saves = store.saves

    summary = await recovery.async_initialize_stop_recovery(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        now=END + timedelta(seconds=10),
    )
    current = await repository.async_get_by_start_lifecycle_id(owned.start_lifecycle_id)

    assert summary.status == recovery.STOP_RECOVERY_OK
    assert summary.transitioned_to_recovery == 1
    assert summary.recovery_required == 1
    assert current is not None
    assert current.state == STOP_STATE_RECOVERY_REQUIRED
    assert current.service_call_status == STOP_CALL_UNKNOWN
    assert current.as_dict()["service_call_performed"] is None
    assert store.saves == before_saves + 1


@pytest.mark.asyncio
async def test_confirmed_stop_dispatch_is_preserved_for_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned = _owned()
    dispatched = replace(
        owned,
        state=STOP_STATE_DISPATCHED,
        service_call_status=STOP_CALL_CONFIRMED,
        dispatch_attempts=1,
        dispatch_started_at=int(END.timestamp()),
        dispatch_confirmed_at=int(END.timestamp()) + 1,
        updated_at=int(END.timestamp()) + 1,
    ).validated()
    store, repository = await _repository_with(dispatched)
    monkeypatch.setattr(recovery, "stop_lifecycle_repository", lambda hass, entry_id: repository)
    hass = _FakeHass()
    before_saves = store.saves

    summary = await recovery.async_initialize_stop_recovery(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        now=END + timedelta(seconds=10),
    )
    current = await repository.async_get_by_start_lifecycle_id(owned.start_lifecycle_id)

    assert summary.status == recovery.STOP_RECOVERY_OK
    assert summary.transitioned_to_recovery == 0
    assert summary.dispatched_pending_verification == 1
    assert current == dispatched
    assert store.saves == before_saves


@pytest.mark.asyncio
async def test_stop_recovery_store_failure_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned = _owned()
    dispatching = replace(
        owned,
        state=STOP_STATE_DISPATCHING,
        service_call_status=STOP_CALL_UNKNOWN,
        dispatch_attempts=1,
        dispatch_started_at=int(END.timestamp()),
        updated_at=int(END.timestamp()),
    ).validated()
    store, repository = await _repository_with(dispatching)
    store.fail_save = True
    monkeypatch.setattr(recovery, "stop_lifecycle_repository", lambda hass, entry_id: repository)
    hass = _FakeHass()

    summary = await recovery.async_initialize_stop_recovery(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        now=END + timedelta(seconds=10),
    )
    current = await repository.async_get_by_start_lifecycle_id(owned.start_lifecycle_id)

    assert summary.status == recovery.STOP_RECOVERY_FAILED
    assert "storage unavailable" in str(summary.error)
    assert current is not None
    assert current.state == STOP_STATE_DISPATCHING
    with pytest.raises(recovery.StopRecoveryBlockedError, match="recovery is failed"):
        recovery.assert_stop_recovery_ready(hass, "entry-1")  # type: ignore[arg-type]


def test_missing_stop_recovery_initialization_is_fail_closed() -> None:
    hass = _FakeHass()
    summary = recovery.stop_recovery_summary(hass, "entry-1")  # type: ignore[arg-type]
    assert summary.status == recovery.STOP_RECOVERY_NOT_INITIALIZED
    with pytest.raises(recovery.StopRecoveryBlockedError, match="not_initialized"):
        recovery.assert_stop_recovery_ready(hass, "entry-1")  # type: ignore[arg-type]
