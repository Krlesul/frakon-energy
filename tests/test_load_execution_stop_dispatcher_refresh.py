from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_stop_dispatcher as dispatcher
from custom_components.frakon_energy.load_execution_stop_lifecycle import (
    ExecutionStopLifecycleRecord,
    ExecutionStopLifecycleRepository,
)

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
        self.value = "on"

    def get(self, entity_id: str):
        return SimpleNamespace(state=self.value)


class _Services:
    def __init__(self, states: _States, refresh_calls: list[str]) -> None:
        self.states = states
        self.refresh_calls = refresh_calls
        self.calls = 0

    async def async_call(self, *args, **kwargs) -> None:
        self.calls += 1
        # Scheduler must not be refreshed after persisting dispatching but before
        # the physical call, nor while confirmed evidence is still incomplete.
        assert self.refresh_calls == []
        self.states.value = "off"


class _Hass:
    def __init__(self, refresh_calls: list[str]) -> None:
        self.data: dict[str, Any] = {}
        self.states = _States()
        self.services = _Services(self.states, refresh_calls)


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


async def _repo() -> ExecutionStopLifecycleRepository:
    repository = ExecutionStopLifecycleRepository(_Store())
    await repository.async_create_owned(_owned())
    return repository


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    repository: ExecutionStopLifecycleRepository,
    refresh_calls: list[str],
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
        refresh_calls.append(entry_id)

    monkeypatch.setattr(
        dispatcher,
        "async_refresh_stop_scheduler_if_started",
        refresh,
    )


@pytest.mark.asyncio
async def test_scheduler_refresh_happens_only_after_verified_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refresh_calls: list[str] = []
    repository = await _repo()
    _patch(monkeypatch, repository, refresh_calls)
    hass = _Hass(refresh_calls)

    result = await dispatcher.async_dispatch_due_stop(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        start_lifecycle_id=_owned().start_lifecycle_id,
        now=END,
    )

    assert result["status"] == "stop_verified"
    assert hass.services.calls == 1
    assert refresh_calls == ["entry-1"]


@pytest.mark.asyncio
async def test_internal_scheduler_can_disable_dispatcher_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refresh_calls: list[str] = []
    repository = await _repo()
    _patch(monkeypatch, repository, refresh_calls)
    hass = _Hass(refresh_calls)

    result = await dispatcher.async_dispatch_due_stop(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        start_lifecycle_id=_owned().start_lifecycle_id,
        now=END,
        refresh_scheduler=False,
    )

    assert result["status"] == "stop_verified"
    assert hass.services.calls == 1
    assert refresh_calls == []
