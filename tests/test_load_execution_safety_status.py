from types import SimpleNamespace

import pytest

from custom_components.frakon_energy import load_execution_safety_status as safety


class _States:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def get(self, entity_id: str):
        return SimpleNamespace(state=self.value) if self.value is not None else None


class _Hass:
    def __init__(self, value: str | None = "off") -> None:
        self.data = {}
        self.states = _States(value)


class _Record:
    def __init__(self, state: str, lifecycle_id: str = "life-1") -> None:
        self.attempt_id = "attempt-1"
        self.lifecycle_id = lifecycle_id
        self.state = state
        self.entity_id = "switch.enyaq_charging"

    def as_dict(self):
        return {"service_call_performed": None if self.state == "recovery_required" else False}


class _StartRepo:
    def __init__(self, records):
        self.records = tuple(records)

    async def async_list(self):
        return self.records


class _StopRepo:
    def __init__(self, stop=None):
        self.stop = stop

    async def async_get_by_start_lifecycle_id(self, lifecycle_id):
        return self.stop


class _Summary:
    def __init__(self, status: str):
        self.status = status

    def as_dict(self):
        return {"status": self.status}


class _SchedulerStatus:
    def __init__(self, start_lifecycle_id: str, status: str):
        self.start_lifecycle_id = start_lifecycle_id
        self.status = status

    def as_dict(self):
        return {
            "start_lifecycle_id": self.start_lifecycle_id,
            "status": self.status,
        }


class _Scheduler:
    def __init__(self, *, started=True, healthy=True):
        self.started = started
        self.healthy = healthy
        self.last_error = None if healthy else "scheduler failed"
        self._statuses = (_SchedulerStatus("life-1", "scheduled"),)

    def statuses(self):
        return self._statuses


class _StartScheduler:
    def __init__(self, *, started=True, healthy=True):
        self.started = started
        self.healthy = healthy
        self.last_error = None if healthy else "start scheduler failed"

    def statuses(self):
        return ()


def _proof(ready: bool, reason: str):
    return SimpleNamespace(
        ownership_ready=ready,
        reason=reason,
    )


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    record_state: str,
    ownership_ready: bool,
    scheduler_healthy: bool = True,
    start_scheduler_healthy: bool = True,
    armed: bool = True,
    arm_storage_healthy: bool = True,
):
    record = _Record(record_state)
    monkeypatch.setattr(
        safety,
        "lifecycle_repository",
        lambda hass, entry_id: _StartRepo([record]),
    )
    monkeypatch.setattr(
        safety,
        "stop_lifecycle_repository",
        lambda hass, entry_id: _StopRepo(
            SimpleNamespace(state="owned") if ownership_ready else None
        ),
    )
    monkeypatch.setattr(
        safety,
        "lifecycle_recovery_summary",
        lambda hass, entry_id: _Summary("ok"),
    )
    monkeypatch.setattr(
        safety,
        "stop_recovery_summary",
        lambda hass, entry_id: _Summary("ok"),
    )
    monkeypatch.setattr(
        safety,
        "stop_scheduler",
        lambda hass, entry_id: _Scheduler(healthy=scheduler_healthy),
    )
    monkeypatch.setattr(
        safety,
        "start_scheduler",
        lambda hass, entry_id: _StartScheduler(healthy=start_scheduler_healthy),
    )

    async def arm_status(hass, entry_id):
        return {
            "entry_id": entry_id,
            "armed": armed,
            "storage_healthy": arm_storage_healthy,
            "last_error": None if arm_storage_healthy else "arm store unavailable",
            "revision": 1 if armed else 0,
            "changed_at": 1 if armed else 0,
            "changed_by": "admin" if armed else None,
            "required_arm_confirmation": "ARM",
            "fail_closed": True,
        }

    monkeypatch.setattr(safety, "async_execution_arm_status", arm_status)

    async def ownership(hass, *, entry_id, start):
        return _proof(
            ownership_ready,
            "stop_ownership_ready" if ownership_ready else "stop_lifecycle_missing",
        )

    monkeypatch.setattr(safety, "async_start_stop_ownership_proof", ownership)


@pytest.mark.asyncio
async def test_prepared_start_without_stop_ownership_is_not_unsafe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(
        monkeypatch,
        record_state="prepared",
        ownership_ready=False,
    )

    result = await safety.async_execution_safety_status(
        _Hass("off"),  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    item = result["items"][0]
    assert item["stop_ownership_required"] is False
    assert item["stop_ownership_ready"] is False
    assert item["safety_status"] == "safe"
    assert item["unsafe_reason"] is None
    assert result["unsafe_start_lifecycles"] == []
    assert result["execution_armed"] is True
    assert result["autonomous_start_enabled"] is True


@pytest.mark.asyncio
async def test_recovered_start_without_stop_ownership_is_explicitly_unsafe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(
        monkeypatch,
        record_state="recovery_required",
        ownership_ready=False,
    )

    result = await safety.async_execution_safety_status(
        _Hass("on"),  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    item = result["items"][0]
    assert item["stop_ownership_required"] is True
    assert item["stop_ownership_ready"] is False
    assert item["safety_status"] == "unsafe"
    assert item["unsafe_reason"] == "bounded_stop_ownership_not_ready:stop_lifecycle_missing"
    assert result["unsafe_start_lifecycles"] == ["life-1"]


@pytest.mark.asyncio
async def test_recovered_start_with_matching_stop_ownership_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(
        monkeypatch,
        record_state="recovery_required",
        ownership_ready=True,
    )

    result = await safety.async_execution_safety_status(
        _Hass("on"),  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    item = result["items"][0]
    assert item["stop_ownership_required"] is True
    assert item["stop_ownership_ready"] is True
    assert item["stop_lifecycle_state"] == "owned"
    assert item["safety_status"] == "safe"
    assert result["unsafe_start_lifecycles"] == []


@pytest.mark.asyncio
async def test_disarmed_interlock_disables_new_start_without_disabling_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(
        monkeypatch,
        record_state="prepared",
        ownership_ready=False,
        armed=False,
    )

    result = await safety.async_execution_safety_status(
        _Hass("off"),  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert result["execution_armed"] is False
    assert result["execution_arm"]["storage_healthy"] is True
    assert result["explicit_start_executor_available"] is False
    assert result["autonomous_start_enabled"] is False
    assert result["autonomous_stop_enabled"] is True
    assert result["explicit_stop_executor_available"] is True


@pytest.mark.asyncio
async def test_arm_storage_failure_is_fail_closed_in_global_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(
        monkeypatch,
        record_state="prepared",
        ownership_ready=False,
        armed=True,
        arm_storage_healthy=False,
    )

    result = await safety.async_execution_safety_status(
        _Hass("off"),  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert result["execution_armed"] is False
    assert result["execution_arm"]["storage_healthy"] is False
    assert result["explicit_start_executor_available"] is False
    assert result["autonomous_start_enabled"] is False
    assert result["autonomous_stop_enabled"] is True


@pytest.mark.asyncio
async def test_unhealthy_stop_scheduler_is_visible_in_global_runtime_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(
        monkeypatch,
        record_state="prepared",
        ownership_ready=False,
        scheduler_healthy=False,
    )

    result = await safety.async_execution_safety_status(
        _Hass("off"),  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert result["start_runtime_ready"] is True
    assert result["stop_runtime_ready"] is False
    assert result["autonomous_stop_enabled"] is False
    assert result["stop_scheduler"]["healthy"] is False
    assert result["explicit_start_executor_available"] is False
    assert result["read_only"] is True
    assert result["state_transition_performed"] is False
    assert result["service_call_performed"] is False
    assert result["execution_performed"] is False