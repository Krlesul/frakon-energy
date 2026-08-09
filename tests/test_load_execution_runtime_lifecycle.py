from __future__ import annotations

import pytest

from custom_components.frakon_energy import load_execution_runtime_lifecycle as lifecycle


class _Hass:
    pass


@pytest.mark.asyncio
async def test_startup_runs_in_order_and_stop_runs_in_reverse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def start_stop(hass, entry_id):
        calls.append("start-stop")

    async def stop_stop(hass, entry_id):
        calls.append("stop-stop")

    async def start_start(hass, entry_id):
        calls.append("start-start")

    async def stop_start(hass, entry_id):
        calls.append("stop-start")

    async def retention(hass, *, entry_id):
        calls.append("retention")

    async def start_pending(hass, entry_id):
        calls.append("start-pending")

    async def stop_pending(hass, entry_id):
        calls.append("stop-pending")

    async def start_settlement(hass, entry_id):
        calls.append("start-settlement")

    async def stop_settlement(hass, entry_id):
        calls.append("stop-settlement")

    monkeypatch.setattr(lifecycle, "async_start_stop_scheduler", start_stop)
    monkeypatch.setattr(lifecycle, "async_stop_stop_scheduler", stop_stop)
    monkeypatch.setattr(lifecycle, "async_start_start_scheduler", start_start)
    monkeypatch.setattr(lifecycle, "async_stop_start_scheduler", stop_start)
    monkeypatch.setattr(lifecycle, "async_run_pending_run_retention_best_effort", retention)
    monkeypatch.setattr(lifecycle, "async_start_pending_run_scheduler", start_pending)
    monkeypatch.setattr(lifecycle, "async_stop_pending_run_scheduler", stop_pending)
    monkeypatch.setattr(lifecycle, "async_start_phase_settlement_runtime", start_settlement)
    monkeypatch.setattr(lifecycle, "async_stop_phase_settlement_runtime", stop_settlement)

    hass = _Hass()
    await lifecycle.async_start_execution_runtimes(hass, "entry-1")  # type: ignore[arg-type]
    await lifecycle.async_stop_execution_runtimes(hass, "entry-1")  # type: ignore[arg-type]

    assert calls == [
        "start-stop",
        "start-start",
        "retention",
        "start-pending",
        "start-settlement",
        "stop-settlement",
        "stop-pending",
        "stop-start",
        "stop-stop",
    ]


@pytest.mark.asyncio
async def test_failed_late_startup_rolls_back_only_started_workers_in_reverse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def start_stop(hass, entry_id):
        calls.append("start-stop")

    async def stop_stop(hass, entry_id):
        calls.append("stop-stop")

    async def start_start(hass, entry_id):
        calls.append("start-start")

    async def stop_start(hass, entry_id):
        calls.append("stop-start")

    async def retention(hass, *, entry_id):
        calls.append("retention")

    async def start_pending(hass, entry_id):
        calls.append("start-pending")

    async def stop_pending(hass, entry_id):
        calls.append("stop-pending")

    async def start_settlement(hass, entry_id):
        calls.append("start-settlement")
        raise RuntimeError("settlement startup failed")

    async def stop_settlement(hass, entry_id):
        calls.append("stop-settlement")

    monkeypatch.setattr(lifecycle, "async_start_stop_scheduler", start_stop)
    monkeypatch.setattr(lifecycle, "async_stop_stop_scheduler", stop_stop)
    monkeypatch.setattr(lifecycle, "async_start_start_scheduler", start_start)
    monkeypatch.setattr(lifecycle, "async_stop_start_scheduler", stop_start)
    monkeypatch.setattr(lifecycle, "async_run_pending_run_retention_best_effort", retention)
    monkeypatch.setattr(lifecycle, "async_start_pending_run_scheduler", start_pending)
    monkeypatch.setattr(lifecycle, "async_stop_pending_run_scheduler", stop_pending)
    monkeypatch.setattr(lifecycle, "async_start_phase_settlement_runtime", start_settlement)
    monkeypatch.setattr(lifecycle, "async_stop_phase_settlement_runtime", stop_settlement)

    with pytest.raises(RuntimeError, match="settlement startup failed"):
        await lifecycle.async_start_execution_runtimes(_Hass(), "entry-1")  # type: ignore[arg-type]

    assert calls == [
        "start-stop",
        "start-start",
        "retention",
        "start-pending",
        "start-settlement",
        "stop-pending",
        "stop-start",
        "stop-stop",
    ]


@pytest.mark.asyncio
async def test_rollback_cleanup_failure_never_masks_original_startup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def ok(hass, entry_id):
        return None

    async def retention(hass, *, entry_id):
        return None

    async def fail_start(hass, entry_id):
        raise RuntimeError("primary startup failure")

    async def fail_stop(hass, entry_id):
        raise RuntimeError("cleanup failure")

    monkeypatch.setattr(lifecycle, "async_start_stop_scheduler", ok)
    monkeypatch.setattr(lifecycle, "async_stop_stop_scheduler", fail_stop)
    monkeypatch.setattr(lifecycle, "async_start_start_scheduler", fail_start)
    monkeypatch.setattr(lifecycle, "async_stop_start_scheduler", ok)
    monkeypatch.setattr(lifecycle, "async_run_pending_run_retention_best_effort", retention)

    with pytest.raises(RuntimeError, match="primary startup failure"):
        await lifecycle.async_start_execution_runtimes(_Hass(), "entry-1")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_stop_attempts_every_runtime_and_reraises_first_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fail_settlement(hass, entry_id):
        calls.append("stop-settlement")
        raise RuntimeError("settlement stop failed")

    async def fail_pending(hass, entry_id):
        calls.append("stop-pending")
        raise RuntimeError("pending stop failed")

    async def stop_start(hass, entry_id):
        calls.append("stop-start")

    async def stop_stop(hass, entry_id):
        calls.append("stop-stop")

    monkeypatch.setattr(lifecycle, "async_stop_phase_settlement_runtime", fail_settlement)
    monkeypatch.setattr(lifecycle, "async_stop_pending_run_scheduler", fail_pending)
    monkeypatch.setattr(lifecycle, "async_stop_start_scheduler", stop_start)
    monkeypatch.setattr(lifecycle, "async_stop_stop_scheduler", stop_stop)

    with pytest.raises(RuntimeError, match="settlement stop failed"):
        await lifecycle.async_stop_execution_runtimes(_Hass(), "entry-1")  # type: ignore[arg-type]

    assert calls == [
        "stop-settlement",
        "stop-pending",
        "stop-start",
        "stop-stop",
    ]
