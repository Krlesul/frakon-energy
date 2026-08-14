from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_stop_resolution as resolution
from custom_components.frakon_energy.load_execution_stop_lifecycle import (
    STOP_CALL_CONFIRMED,
    STOP_CALL_UNKNOWN,
    STOP_STATE_DISPATCHED,
    STOP_STATE_RECOVERY_REQUIRED,
    STOP_STATE_SATISFIED,
    STOP_STATE_VERIFIED,
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
        self.fail_save = False

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        if self.fail_save:
            raise RuntimeError("storage unavailable")
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


def _recovery_required() -> ExecutionStopLifecycleRecord:
    return replace(
        _owned(),
        state=STOP_STATE_RECOVERY_REQUIRED,
        service_call_status=STOP_CALL_UNKNOWN,
        dispatch_attempts=1,
        dispatch_started_at=int(END.timestamp()),
        updated_at=int(END.timestamp()),
    ).validated()


def _dispatched() -> ExecutionStopLifecycleRecord:
    return replace(
        _owned(),
        state=STOP_STATE_DISPATCHED,
        service_call_status=STOP_CALL_CONFIRMED,
        dispatch_attempts=1,
        dispatch_started_at=int(END.timestamp()),
        dispatch_confirmed_at=int(END.timestamp()) + 1,
        updated_at=int(END.timestamp()) + 1,
    ).validated()


async def _repository_with(record: ExecutionStopLifecycleRecord):
    store = _FakeStore()
    repository = ExecutionStopLifecycleRepository(store)
    await repository.async_create_owned(_owned())
    if record.state != "owned":
        if record.state in (STOP_STATE_RECOVERY_REQUIRED, STOP_STATE_DISPATCHED):
            dispatching = replace(
                _owned(),
                state="dispatching",
                service_call_status=STOP_CALL_UNKNOWN,
                dispatch_attempts=1,
                dispatch_started_at=int(END.timestamp()),
                updated_at=int(END.timestamp()),
            ).validated()
            await repository.async_update(dispatching)
        await repository.async_update(record)
    return store, repository


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    repository: ExecutionStopLifecycleRepository,
) -> None:
    monkeypatch.setattr(resolution, "stop_lifecycle_repository", lambda hass, entry_id: repository)
    monkeypatch.setattr(resolution, "assert_stop_recovery_ready", lambda hass, entry_id: None)


@pytest.mark.asyncio
async def test_complete_noop_persists_satisfied_without_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repository = await _repository_with(_owned())
    _patch_runtime(monkeypatch, repository)
    hass = _Hass("off")

    result = await resolution.async_complete_stop_noop(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        start_lifecycle_id=_owned().start_lifecycle_id,
        now=END,
    )
    current = await repository.async_get_by_start_lifecycle_id(_owned().start_lifecycle_id)

    assert current is not None
    assert current.state == STOP_STATE_SATISFIED
    assert current.dispatch_attempts == 0
    assert result["state_transition_performed"] is True
    assert result["service_call_performed"] is False
    assert result["execution_performed"] is False
    assert store.saves == 2


@pytest.mark.asyncio
async def test_complete_noop_rejects_before_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, repository = await _repository_with(_owned())
    _patch_runtime(monkeypatch, repository)
    hass = _Hass("off")

    with pytest.raises(resolution.StopResolutionError, match="waiting"):
        await resolution.async_complete_stop_noop(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            start_lifecycle_id=_owned().start_lifecycle_id,
            now=END - timedelta(seconds=1),
        )


@pytest.mark.asyncio
async def test_noop_retry_is_idempotent_even_if_live_state_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repository = await _repository_with(_owned())
    _patch_runtime(monkeypatch, repository)
    hass = _Hass("off")
    first = await resolution.async_complete_stop_noop(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        start_lifecycle_id=_owned().start_lifecycle_id,
        now=END,
    )
    saves = store.saves
    hass.states.value = "on"

    second = await resolution.async_complete_stop_noop(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        start_lifecycle_id=_owned().start_lifecycle_id,
        now=END + timedelta(minutes=1),
    )

    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert second["state_transition_performed"] is False
    assert store.saves == saves


@pytest.mark.asyncio
async def test_verify_unknown_recovery_preserves_unknown_call_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, repository = await _repository_with(_recovery_required())
    _patch_runtime(monkeypatch, repository)
    hass = _Hass("off")

    result = await resolution.async_verify_stop_resolution(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        start_lifecycle_id=_owned().start_lifecycle_id,
        now=END + timedelta(seconds=5),
    )
    current = await repository.async_get_by_start_lifecycle_id(_owned().start_lifecycle_id)

    assert current is not None
    assert current.state == STOP_STATE_VERIFIED
    assert current.service_call_status == STOP_CALL_UNKNOWN
    assert result["service_call_performed"] is None
    assert result["execution_performed"] is False


@pytest.mark.asyncio
async def test_verify_confirmed_dispatch_preserves_confirmed_call_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, repository = await _repository_with(_dispatched())
    _patch_runtime(monkeypatch, repository)
    hass = _Hass("off")

    result = await resolution.async_verify_stop_resolution(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        start_lifecycle_id=_owned().start_lifecycle_id,
        now=END + timedelta(seconds=5),
    )

    assert result["stop_lifecycle"]["state"] == STOP_STATE_VERIFIED
    assert result["service_call_performed"] is True
    assert result["execution_performed"] is False


@pytest.mark.asyncio
async def test_verify_rejects_live_state_that_is_still_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, repository = await _repository_with(_recovery_required())
    _patch_runtime(monkeypatch, repository)
    hass = _Hass("on")

    with pytest.raises(resolution.StopResolutionError, match="recovery_review"):
        await resolution.async_verify_stop_resolution(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            start_lifecycle_id=_owned().start_lifecycle_id,
            now=END + timedelta(seconds=5),
        )


@pytest.mark.asyncio
async def test_verify_retry_is_idempotent_without_rechecking_live_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repository = await _repository_with(_recovery_required())
    _patch_runtime(monkeypatch, repository)
    hass = _Hass("off")
    await resolution.async_verify_stop_resolution(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        start_lifecycle_id=_owned().start_lifecycle_id,
        now=END + timedelta(seconds=5),
    )
    saves = store.saves
    hass.states.value = "on"

    replay = await resolution.async_verify_stop_resolution(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        start_lifecycle_id=_owned().start_lifecycle_id,
        now=END + timedelta(minutes=1),
    )

    assert replay["idempotent_replay"] is True
    assert replay["state_transition_performed"] is False
    assert replay["service_call_performed"] is None
    assert store.saves == saves


@pytest.mark.asyncio
async def test_noop_store_failure_rolls_back_owned_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repository = await _repository_with(_owned())
    _patch_runtime(monkeypatch, repository)
    hass = _Hass("off")
    store.fail_save = True

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await resolution.async_complete_stop_noop(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            start_lifecycle_id=_owned().start_lifecycle_id,
            now=END,
        )

    current = await repository.async_get_by_start_lifecycle_id(_owned().start_lifecycle_id)
    assert current == _owned()


@pytest.mark.asyncio
async def test_verify_store_failure_rolls_back_recovery_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repository = await _repository_with(_recovery_required())
    _patch_runtime(monkeypatch, repository)
    hass = _Hass("off")
    store.fail_save = True

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await resolution.async_verify_stop_resolution(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            start_lifecycle_id=_owned().start_lifecycle_id,
            now=END + timedelta(seconds=5),
        )

    current = await repository.async_get_by_start_lifecycle_id(_owned().start_lifecycle_id)
    assert current == _recovery_required()
