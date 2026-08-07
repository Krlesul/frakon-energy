from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_stop_dispatcher as dispatcher
from custom_components.frakon_energy.load_execution_stop_lifecycle import (
    STOP_CALL_CONFIRMED,
    STOP_CALL_UNKNOWN,
    STOP_STATE_DISPATCHED,
    STOP_STATE_RECOVERY_REQUIRED,
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
        self.fail_on_saves: set[int] = set()

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        next_save = self.saves + 1
        if next_save in self.fail_on_saves:
            self.saves += 1
            raise RuntimeError(f"storage unavailable on save {next_save}")
        self.data = data
        self.saves += 1


class _States:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def get(self, entity_id: str) -> object | None:
        return SimpleNamespace(state=self.value) if self.value is not None else None


class _Services:
    def __init__(self, states: _States) -> None:
        self.states = states
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None
        self.set_state_after_call: str | None = "off"

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
        if self.error is not None:
            raise self.error
        if self.set_state_after_call is not None:
            self.states.value = self.set_state_after_call


class _Hass:
    def __init__(self, state: str | None = "on") -> None:
        self.data: dict[str, Any] = {}
        self.states = _States(state)
        self.services = _Services(self.states)


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


async def _repository_with(record: ExecutionStopLifecycleRecord):
    store = _FakeStore()
    repository = ExecutionStopLifecycleRepository(store)
    await repository.async_create_owned(_owned())
    if record.state != "owned":
        await repository.async_update(record)
    return store, repository


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    repository: ExecutionStopLifecycleRepository,
) -> None:
    monkeypatch.setattr(
        dispatcher,
        "stop_lifecycle_repository",
        lambda hass, entry_id: repository,
    )
    monkeypatch.setattr(
        dispatcher,
        "assert_stop_recovery_ready",
        lambda hass, entry_id: None,
    )

    async def refresh(hass, entry_id):
        return None

    monkeypatch.setattr(dispatcher, "async_refresh_stop_scheduler_if_started", refresh)


@pytest.mark.asyncio
async def test_physical_stop_persists_before_call_uses_only_immutable_target_and_verifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repository = await _repository_with(_owned())
    _patch_runtime(monkeypatch, repository)
    hass = _Hass("on")
    context = object()

    result = await dispatcher.async_dispatch_due_stop(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        start_lifecycle_id=_owned().start_lifecycle_id,
        context=context,  # type: ignore[arg-type]
        now=END,
    )
    current = await repository.async_get_by_start_lifecycle_id(_owned().start_lifecycle_id)

    assert len(hass.services.calls) == 1
    assert hass.services.calls[0] == {
        "domain": "switch",
        "service": "turn_off",
        "service_data": {},
        "blocking": True,
        "context": context,
        "target": {"entity_id": "switch.enyaq_charging"},
    }
    assert current is not None
    assert current.state == STOP_STATE_VERIFIED
    assert current.service_call_status == STOP_CALL_CONFIRMED
    assert current.dispatch_attempts == 1
    assert result["status"] == "stop_verified"
    assert result["service_call_performed"] is True
    assert result["execution_performed"] is True
    assert result["can_retry_unknown"] is False
    # create owned + dispatching + confirmed + verified
    assert store.saves == 4


@pytest.mark.asyncio
async def test_normal_call_with_state_still_on_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, repository = await _repository_with(_owned())
    _patch_runtime(monkeypatch, repository)
    hass = _Hass("on")
    hass.services.set_state_after_call = "on"

    result = await dispatcher.async_dispatch_due_stop(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        start_lifecycle_id=_owned().start_lifecycle_id,
        now=END,
    )
    assert result["status"] == "stop_dispatched_pending_verification"
    assert len(hass.services.calls) == 1
    current = await repository.async_get_by_start_lifecycle_id(_owned().start_lifecycle_id)
    assert current is not None
    assert current.state == STOP_STATE_DISPATCHED

    with pytest.raises(dispatcher.StopDispatchError, match="cannot be retried"):
        await dispatcher.async_dispatch_due_stop(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            start_lifecycle_id=_owned().start_lifecycle_id,
            now=END + timedelta(seconds=1),
        )
    assert len(hass.services.calls) == 1


@pytest.mark.asyncio
async def test_service_exception_becomes_recovery_required_and_never_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, repository = await _repository_with(_owned())
    _patch_runtime(monkeypatch, repository)
    hass = _Hass("on")
    hass.services.error = RuntimeError("transport failed")

    with pytest.raises(dispatcher.StopDispatchUnknownOutcomeError, match="outcome is unknown"):
        await dispatcher.async_dispatch_due_stop(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            start_lifecycle_id=_owned().start_lifecycle_id,
            now=END,
        )

    current = await repository.async_get_by_start_lifecycle_id(_owned().start_lifecycle_id)
    assert current is not None
    assert current.state == STOP_STATE_RECOVERY_REQUIRED
    assert current.service_call_status == STOP_CALL_UNKNOWN
    assert current.as_dict()["service_call_performed"] is None
    assert len(hass.services.calls) == 1

    hass.services.error = None
    with pytest.raises(dispatcher.StopDispatchError, match="cannot be retried"):
        await dispatcher.async_dispatch_due_stop(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            start_lifecycle_id=_owned().start_lifecycle_id,
            now=END + timedelta(seconds=1),
        )
    assert len(hass.services.calls) == 1


@pytest.mark.asyncio
async def test_dispatching_persistence_failure_prevents_physical_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repository = await _repository_with(_owned())
    _patch_runtime(monkeypatch, repository)
    hass = _Hass("on")
    # save 1 created owned; save 2 would persist dispatching before the service boundary.
    store.fail_on_saves.add(2)

    with pytest.raises(RuntimeError, match="storage unavailable on save 2"):
        await dispatcher.async_dispatch_due_stop(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            start_lifecycle_id=_owned().start_lifecycle_id,
            now=END,
        )

    assert hass.services.calls == []
    current = await repository.async_get_by_start_lifecycle_id(_owned().start_lifecycle_id)
    assert current == _owned()


@pytest.mark.asyncio
async def test_confirm_persistence_failure_converts_to_unknown_recovery_without_second_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repository = await _repository_with(_owned())
    _patch_runtime(monkeypatch, repository)
    hass = _Hass("on")
    hass.services.set_state_after_call = "off"
    # save 2 dispatching succeeds; save 3 confirmed fails; save 4 recovery succeeds.
    store.fail_on_saves.add(3)

    with pytest.raises(dispatcher.StopDispatchUnknownOutcomeError, match="confirmed evidence"):
        await dispatcher.async_dispatch_due_stop(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            start_lifecycle_id=_owned().start_lifecycle_id,
            now=END,
        )

    assert len(hass.services.calls) == 1
    current = await repository.async_get_by_start_lifecycle_id(_owned().start_lifecycle_id)
    assert current is not None
    assert current.state == STOP_STATE_RECOVERY_REQUIRED
    assert current.service_call_status == STOP_CALL_UNKNOWN
    assert current.as_dict()["service_call_performed"] is None


@pytest.mark.asyncio
async def test_unknown_recovery_off_can_verify_without_redispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, repository = await _repository_with(_recovery_required())
    _patch_runtime(monkeypatch, repository)
    hass = _Hass("off")

    result = await dispatcher.async_dispatch_due_stop(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        start_lifecycle_id=_owned().start_lifecycle_id,
        now=END + timedelta(seconds=5),
    )

    assert hass.services.calls == []
    assert result["status"] == "verified_without_redispatch"
    assert result["service_call_performed"] is None
    assert result["execution_performed"] is False
    current = await repository.async_get_by_start_lifecycle_id(_owned().start_lifecycle_id)
    assert current is not None
    assert current.state == STOP_STATE_VERIFIED
    assert current.service_call_status == STOP_CALL_UNKNOWN


@pytest.mark.asyncio
async def test_due_already_off_uses_noop_without_physical_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, repository = await _repository_with(_owned())
    _patch_runtime(monkeypatch, repository)
    hass = _Hass("off")
    called: list[str] = []

    async def noop(hass, *, entry_id, start_lifecycle_id, now):
        called.append(start_lifecycle_id)
        return {
            "stop_lifecycle": _owned().as_dict(),
            "idempotent_replay": False,
            "resolution_performed": True,
            "state_transition_performed": True,
            "service_call_performed": False,
            "execution_performed": False,
        }

    monkeypatch.setattr(dispatcher, "async_complete_stop_noop", noop)

    result = await dispatcher.async_dispatch_due_stop(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        start_lifecycle_id=_owned().start_lifecycle_id,
        now=END,
    )

    assert called == [_owned().start_lifecycle_id]
    assert hass.services.calls == []
    assert result["status"] == "already_off_no_dispatch"
    assert result["physical_dispatch_attempted"] is False
    assert result["service_call_performed"] is False


@pytest.mark.asyncio
async def test_before_deadline_and_unavailable_state_never_call_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, repository = await _repository_with(_owned())
    _patch_runtime(monkeypatch, repository)
    hass = _Hass("on")

    with pytest.raises(dispatcher.StopDispatchError, match="not ready"):
        await dispatcher.async_dispatch_due_stop(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            start_lifecycle_id=_owned().start_lifecycle_id,
            now=END - timedelta(seconds=1),
        )
    assert hass.services.calls == []

    hass.states.value = "unavailable"
    with pytest.raises(dispatcher.StopDispatchError, match="not ready"):
        await dispatcher.async_dispatch_due_stop(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            start_lifecycle_id=_owned().start_lifecycle_id,
            now=END,
        )
    assert hass.services.calls == []
