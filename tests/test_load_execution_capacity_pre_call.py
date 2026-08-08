from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.frakon_energy import load_execution_arm as arm
from custom_components.frakon_energy import load_execution_lifecycle_runtime as lifecycle_runtime
from custom_components.frakon_energy import load_execution_site_capacity_gate as capacity_gate
from custom_components.frakon_energy import site_capacity
from custom_components.frakon_energy.load_execution_arm import ExecutionCapacityBlockedError
from custom_components.frakon_energy.load_execution_site_capacity_gate import (
    CAPACITY_GATE_BLOCKED,
    CAPACITY_GATE_READY,
    REASON_INSUFFICIENT_HEADROOM,
    REASON_READY,
    SiteCapacityGateDecision,
)
from custom_components.frakon_energy.site_capacity import SiteCapacityStatus


class _ConfigEntries:
    def __init__(self, options: dict) -> None:
        self.entry = SimpleNamespace(entry_id="entry-1", options=options)

    def async_get_entry(self, entry_id: str):
        return self.entry if entry_id == "entry-1" else None


class _Hass:
    def __init__(self, options: dict) -> None:
        self.config_entries = _ConfigEntries(options)
        self.data = {}
        # Intentionally no services registry: this helper must never dispatch.


class _Repo:
    def __init__(self, records) -> None:
        self.records = tuple(records)

    async def async_list(self):
        return self.records


def _options(*, guard: bool) -> dict:
    return {
        "site_capacity": {
            "max_grid_import_kw": 12.0,
            "execution_guard_enabled": guard,
        }
    }


def _capacity() -> SiteCapacityStatus:
    return SiteCapacityStatus(
        entry_id="entry-1",
        status="within_limit",
        configured=True,
        topology_ready=True,
        source_available=True,
        max_grid_import_kw=12.0,
        current_grid_import_kw=5.0,
        grid_headroom_kw=7.0,
        grid_over_limit_kw=0.0,
        utilization_percent=41.67,
        source_entity_id="sensor.grid_in",
        reason="ok",
        execution_guard_active=True,
    )


def _decision(*, blocked: bool) -> SiteCapacityGateDecision:
    return SiteCapacityGateDecision(
        status=CAPACITY_GATE_BLOCKED if blocked else CAPACITY_GATE_READY,
        reason=REASON_INSUFFICIENT_HEADROOM if blocked else REASON_READY,
        capacity_status="within_limit",
        configured=True,
        planned_power_kw=11.0,
        current_grid_import_kw=5.0,
        max_grid_import_kw=12.0,
        grid_headroom_kw=7.0,
        projected_grid_import_kw=16.0 if blocked else 11.0,
        projected_over_limit_kw=4.0 if blocked else 0.0,
        can_start=not blocked,
        guard_active=True,
    )


def _patch_runtime(monkeypatch: pytest.MonkeyPatch, records) -> None:
    monkeypatch.setattr(lifecycle_runtime, "lifecycle_repository", lambda hass, entry_id: _Repo(records))
    monkeypatch.setattr(site_capacity, "build_site_capacity_status", lambda *args, **kwargs: _capacity())


@pytest.mark.asyncio
async def test_disabled_capacity_guard_is_inert_even_with_dispatching_start(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = _Hass(_options(guard=False))
    _patch_runtime(monkeypatch, [SimpleNamespace(state="dispatching", plan=SimpleNamespace(power_kw=11.0))])
    called = False

    def evaluate(**kwargs):
        nonlocal called
        called = True
        return _decision(blocked=True)

    monkeypatch.setattr(capacity_gate, "evaluate_site_capacity_execution_gate", evaluate)
    await arm._async_require_dispatching_site_capacity(hass, "entry-1")  # type: ignore[arg-type]
    assert called is False


@pytest.mark.asyncio
async def test_active_guard_without_dispatching_start_does_not_run_last_moment_check(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = _Hass(_options(guard=True))
    _patch_runtime(monkeypatch, [SimpleNamespace(state="prepared", plan=SimpleNamespace(power_kw=11.0))])
    called = False

    def evaluate(**kwargs):
        nonlocal called
        called = True
        return _decision(blocked=True)

    monkeypatch.setattr(capacity_gate, "evaluate_site_capacity_execution_gate", evaluate)
    await arm._async_require_dispatching_site_capacity(hass, "entry-1")  # type: ignore[arg-type]
    assert called is False


@pytest.mark.asyncio
async def test_active_guard_allows_dispatching_start_when_last_moment_headroom_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = _Hass(_options(guard=True))
    _patch_runtime(monkeypatch, [SimpleNamespace(state="dispatching", plan=SimpleNamespace(power_kw=6.0))])
    monkeypatch.setattr(capacity_gate, "evaluate_site_capacity_execution_gate", lambda **kwargs: _decision(blocked=False))
    await arm._async_require_dispatching_site_capacity(hass, "entry-1")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_active_guard_blocks_dispatching_start_at_final_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = _Hass(_options(guard=True))
    _patch_runtime(monkeypatch, [SimpleNamespace(state="dispatching", plan=SimpleNamespace(power_kw=11.0))])
    monkeypatch.setattr(capacity_gate, "evaluate_site_capacity_execution_gate", lambda **kwargs: _decision(blocked=True))

    with pytest.raises(ExecutionCapacityBlockedError, match="blocked immediately before physical start"):
        await arm._async_require_dispatching_site_capacity(hass, "entry-1")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_multiple_dispatching_starts_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = _Hass(_options(guard=True))
    _patch_runtime(
        monkeypatch,
        [
            SimpleNamespace(state="dispatching", plan=SimpleNamespace(power_kw=1.0)),
            SimpleNamespace(state="dispatching", plan=SimpleNamespace(power_kw=1.0)),
        ],
    )
    with pytest.raises(ExecutionCapacityBlockedError, match="exactly one dispatching start"):
        await arm._async_require_dispatching_site_capacity(hass, "entry-1")  # type: ignore[arg-type]
