from types import SimpleNamespace

import pytest

from custom_components.frakon_energy import load_execution_prepare_scheduler_ws_api as status_ws
from custom_components.frakon_energy.load_execution_lifecycle_recovery import (
    RECOVERY_FAILED,
    RECOVERY_OK,
    LifecycleRecoverySummary,
)
from custom_components.frakon_energy.load_execution_prepare_scheduler import (
    STATUS_SCHEDULED,
    PrepareSchedulerStatus,
)


class _FakeScheduler:
    def __init__(self, *, started: bool, last_error: str | None = None) -> None:
        self.started = started
        self.last_error = last_error

    def statuses(self):
        return (
            PrepareSchedulerStatus(
                attempt_id="attempt-1",
                schedule_id="schedule-1",
                status=STATUS_SCHEDULED,
                next_wake_at="2026-08-07T23:00:00+00:00",
                timer_active=True,
            ),
        )


def _recovery(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    monkeypatch.setattr(
        status_ws,
        "lifecycle_recovery_summary",
        lambda hass, entry_id: LifecycleRecoverySummary(
            entry_id=entry_id,
            status=status,
            scanned=0,
            transitioned_to_recovery=0,
            recovery_required=0,
            dispatched_pending_verification=0,
            error="storage unavailable" if status == RECOVERY_FAILED else None,
        ),
    )


@pytest.mark.asyncio
async def test_scheduler_status_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = _FakeScheduler(started=True)
    monkeypatch.setattr(status_ws, "existing_prepare_scheduler", lambda hass, entry_id: scheduler)
    _recovery(monkeypatch, RECOVERY_OK)

    result = await status_ws.async_prepare_scheduler_status(
        SimpleNamespace(),  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert result["started"] is True
    assert result["last_error"] is None
    assert result["statuses"][0]["status"] == STATUS_SCHEDULED
    assert result["statuses"][0]["timer_active"] is True
    assert result["prepare_only"] is True
    assert result["read_only"] is True
    assert result["state_transition_performed"] is False
    assert result["execution_performed"] is False
    assert result["service_call_performed"] is False
    assert result["executor_available"] is False


@pytest.mark.asyncio
async def test_scheduler_status_surfaces_fail_closed_startup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _FakeScheduler(started=False, last_error="schedule store unavailable")
    monkeypatch.setattr(status_ws, "existing_prepare_scheduler", lambda hass, entry_id: scheduler)
    _recovery(monkeypatch, RECOVERY_FAILED)

    result = await status_ws.async_prepare_scheduler_status(
        SimpleNamespace(),  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert result["started"] is False
    assert result["last_error"] == "schedule store unavailable"
    assert result["recovery"]["status"] == RECOVERY_FAILED


@pytest.mark.asyncio
async def test_scheduler_status_does_not_create_missing_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(status_ws, "existing_prepare_scheduler", lambda hass, entry_id: None)
    _recovery(monkeypatch, RECOVERY_OK)

    result = await status_ws.async_prepare_scheduler_status(
        SimpleNamespace(),  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert result["started"] is False
    assert result["last_error"] is None
    assert result["statuses"] == []
