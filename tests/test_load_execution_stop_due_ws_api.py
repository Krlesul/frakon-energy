from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_stop_due_ws_api as due_ws
from custom_components.frakon_energy import load_execution_stop_recovery as recovery
from custom_components.frakon_energy.load_execution_stop_due_gate import (
    STOP_DUE_ALREADY_OFF,
    STOP_DUE_BLOCKED,
    STOP_DUE_READY,
)
from custom_components.frakon_energy.load_execution_stop_lifecycle import (
    ExecutionStopLifecycleRecord,
    ExecutionStopLifecycleRepository,
)

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)
END = START + timedelta(hours=2)


class _FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.saves = 0

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = data
        self.saves += 1


class _States:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def get(self, entity_id: str) -> object | None:
        return SimpleNamespace(state=self.value) if self.value is not None else None


class _Hass:
    def __init__(self, state: str | None) -> None:
        self.data: dict[str, Any] = {}
        self.states = _States(state)


def _owned() -> ExecutionStopLifecycleRecord:
    return ExecutionStopLifecycleRecord(
        stop_lifecycle_id="f76d6da2c879fda8c5fae44f6cdc897a",
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
        state="owned",
        service_call_status="not_started",
        verification_status="pending",
        created_at=int(START.timestamp()),
        updated_at=int(START.timestamp()),
    ).validated()


def _summary(status: str) -> recovery.StopRecoverySummary:
    return recovery.StopRecoverySummary(
        entry_id="entry-1",
        status=status,
        scanned=1,
        transitioned_to_recovery=0,
        recovery_required=0,
        dispatched_pending_verification=0,
        error="storage unavailable" if status == recovery.STOP_RECOVERY_FAILED else None,
    )


@pytest.mark.asyncio
async def test_due_diagnostics_reads_live_on_state_without_store_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore()
    repository = ExecutionStopLifecycleRepository(store)
    record = _owned()
    await repository.async_create_owned(record)
    before_saves = store.saves
    hass = _Hass("on")
    monkeypatch.setattr(due_ws, "stop_lifecycle_repository", lambda hass, entry_id: repository)
    monkeypatch.setattr(due_ws, "stop_recovery_summary", lambda hass, entry_id: _summary(recovery.STOP_RECOVERY_OK))

    result = await due_ws.async_stop_due_diagnostics(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        now=END,
    )

    assert result["items"][0]["decision"]["status"] == STOP_DUE_READY
    assert result["stop_candidates"] == [record.start_lifecycle_id]
    assert result["noop_candidates"] == []
    assert result["read_only"] is True
    assert result["state_transition_performed"] is False
    assert result["service_call_performed"] is False
    assert store.saves == before_saves


@pytest.mark.asyncio
async def test_due_diagnostics_routes_already_off_to_noop_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore()
    repository = ExecutionStopLifecycleRepository(store)
    record = _owned()
    await repository.async_create_owned(record)
    hass = _Hass("off")
    monkeypatch.setattr(due_ws, "stop_lifecycle_repository", lambda hass, entry_id: repository)
    monkeypatch.setattr(due_ws, "stop_recovery_summary", lambda hass, entry_id: _summary(recovery.STOP_RECOVERY_OK))

    result = await due_ws.async_stop_due_diagnostics(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        now=END,
    )

    assert result["items"][0]["decision"]["status"] == STOP_DUE_ALREADY_OFF
    assert result["stop_candidates"] == []
    assert result["noop_candidates"] == [record.start_lifecycle_id]


@pytest.mark.asyncio
async def test_failed_startup_recovery_removes_all_stop_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore()
    repository = ExecutionStopLifecycleRepository(store)
    record = _owned()
    await repository.async_create_owned(record)
    hass = _Hass("on")
    monkeypatch.setattr(due_ws, "stop_lifecycle_repository", lambda hass, entry_id: repository)
    monkeypatch.setattr(due_ws, "stop_recovery_summary", lambda hass, entry_id: _summary(recovery.STOP_RECOVERY_FAILED))

    result = await due_ws.async_stop_due_diagnostics(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        now=END + timedelta(minutes=1),
    )

    assert result["items"][0]["decision"]["status"] == STOP_DUE_BLOCKED
    assert result["stop_candidates"] == []
    assert result["noop_candidates"] == []
    assert result["verify_candidates"] == []


@pytest.mark.asyncio
async def test_due_diagnostics_can_filter_one_start_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ExecutionStopLifecycleRepository(_FakeStore())
    record = _owned()
    await repository.async_create_owned(record)
    hass = _Hass("on")
    monkeypatch.setattr(due_ws, "stop_lifecycle_repository", lambda hass, entry_id: repository)
    monkeypatch.setattr(due_ws, "stop_recovery_summary", lambda hass, entry_id: _summary(recovery.STOP_RECOVERY_OK))

    result = await due_ws.async_stop_due_diagnostics(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        start_lifecycle_id=record.start_lifecycle_id,
        now=END,
    )
    assert len(result["items"]) == 1

    with pytest.raises(ValueError, match="stop lifecycle not found"):
        await due_ws.async_stop_due_diagnostics(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            start_lifecycle_id="0" * 32,
            now=END,
        )
