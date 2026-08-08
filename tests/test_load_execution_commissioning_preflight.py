from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_commissioning_preflight as preflight
from custom_components.frakon_energy.load_execution_commissioning_preflight import (
    PREFLIGHT_ALREADY_ARMED,
    PREFLIGHT_BLOCKED,
    PREFLIGHT_NO_START_NEEDED,
    PREFLIGHT_READY_FOR_ARM,
)

NOW = datetime(2026, 8, 8, 7, 30, tzinfo=timezone.utc)


class _Guard:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Hass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}


def _safety(*, armed: bool = False, arm_healthy: bool = True, start_healthy: bool = True, stop_healthy: bool = True) -> dict[str, Any]:
    return {
        "execution_arm": {
            "entry_id": "entry-1",
            "armed": armed,
            "storage_healthy": arm_healthy,
            "last_error": None if arm_healthy else "arm storage corrupt",
            "revision": 1 if armed else 0,
            "changed_at": 1 if armed else 0,
            "changed_by": "admin" if armed else None,
            "required_arm_confirmation": "ARM",
            "fail_closed": True,
        },
        "start_recovery": {"status": "ok"},
        "stop_recovery": {"status": "ok"},
        "start_scheduler": {
            "started": True,
            "healthy": start_healthy,
            "last_error": None,
            "statuses": [],
        },
        "stop_scheduler": {
            "started": True,
            "healthy": stop_healthy,
            "last_error": None,
            "statuses": [],
        },
    }


def _gate(*, status: str = "ready_to_start", reason: str = "bounded_start_has_armed_stop_obligation", with_lease: bool = True) -> dict[str, Any]:
    return {
        "lifecycle": {
            "lifecycle_id": "life-1",
            "attempt_id": "attempt-1",
            "entity_id": "switch.enyaq_charging",
            "service_domain": "switch",
            "service_name": "turn_on",
        },
        "stop_lease": {
            "lease_id": "lease-1",
            "entity_id": "switch.enyaq_charging",
            "service_domain": "switch",
            "service_name": "turn_off",
            "ends_at": "2026-08-08T10:00:00+00:00",
        } if with_lease else None,
        "bounded_dispatch_gate": {
            "status": status,
            "reason": reason,
            "stop_lease_matches": with_lease and status == "ready_to_start",
            "dispatch_gate_matches": status in ("ready_to_start", "already_satisfied"),
            "can_start": status == "ready_to_start",
        },
    }


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    safety: dict[str, Any],
    gate: dict[str, Any],
) -> None:
    monkeypatch.setattr(preflight, "execution_arm_guard", lambda hass, entry_id: _Guard())

    async def safety_status(hass, *, entry_id):
        return safety

    async def bounded_gate(hass, *, entry_id, attempt_id, now):
        return gate

    monkeypatch.setattr(preflight, "async_execution_safety_status", safety_status)
    monkeypatch.setattr(preflight, "async_bounded_dispatch_gate", bounded_gate)


@pytest.mark.asyncio
async def test_disarmed_ready_preflight_exposes_exact_actions_without_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, safety=_safety(), gate=_gate())

    result = await preflight.async_execution_commissioning_preflight(
        _Hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=NOW,
    )

    assert result["status"] == PREFLIGHT_READY_FOR_ARM
    assert result["commissioning_window_safe"] is True
    assert result["can_arm_to_execute"] is True
    assert result["arm_is_only_remaining_interlock"] is True
    assert result["immutable_start_action"] == {
        "service_domain": "switch",
        "service_name": "turn_on",
        "entity_id": "switch.enyaq_charging",
        "service_data": {},
    }
    assert result["immutable_stop_action"] == {
        "service_domain": "switch",
        "service_name": "turn_off",
        "entity_id": "switch.enyaq_charging",
        "service_data": {},
        "ends_at": "2026-08-08T10:00:00+00:00",
    }
    assert result["client_supplied_action_fields"] is False
    assert result["preflight_snapshot_reserves_execution"] is False
    assert result["gates_rechecked_after_arm"] is True
    assert result["dry_run"] is True
    assert result["read_only"] is True
    assert result["state_transition_performed"] is False
    assert result["service_call_performed"] is False
    assert result["execution_performed"] is False


@pytest.mark.asyncio
async def test_armed_runtime_is_not_a_safe_commissioning_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, safety=_safety(armed=True), gate=_gate())

    result = await preflight.async_execution_commissioning_preflight(
        _Hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=NOW,
    )

    assert result["status"] == PREFLIGHT_ALREADY_ARMED
    assert result["commissioning_window_safe"] is False
    assert result["can_arm_to_execute"] is False
    assert "execution_is_armed_commissioning_requires_disarmed" in result["reasons"]
    assert result["service_call_performed"] is False


@pytest.mark.asyncio
async def test_missing_stop_lease_stays_blocked_and_never_claims_arm_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(
        monkeypatch,
        safety=_safety(),
        gate=_gate(
            status="blocked",
            reason="durable_stop_lease_required",
            with_lease=False,
        ),
    )

    result = await preflight.async_execution_commissioning_preflight(
        _Hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=NOW,
    )

    assert result["status"] == PREFLIGHT_BLOCKED
    assert result["can_arm_to_execute"] is False
    assert result["immutable_stop_action"] is None
    assert result["durable_stop_lease_present"] is False
    assert "bounded_gate_blocked:durable_stop_lease_required" in result["reasons"]


@pytest.mark.asyncio
async def test_arm_storage_failure_is_fail_closed_even_when_bounded_gate_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, safety=_safety(arm_healthy=False), gate=_gate())

    result = await preflight.async_execution_commissioning_preflight(
        _Hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=NOW,
    )

    assert result["status"] == PREFLIGHT_BLOCKED
    assert result["commissioning_window_safe"] is False
    assert result["can_arm_to_execute"] is False
    assert "execution_arm_storage_unhealthy" in result["reasons"]


@pytest.mark.asyncio
async def test_unhealthy_stop_scheduler_blocks_arm_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, safety=_safety(stop_healthy=False), gate=_gate())

    result = await preflight.async_execution_commissioning_preflight(
        _Hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=NOW,
    )

    assert result["status"] == PREFLIGHT_BLOCKED
    assert result["can_arm_to_execute"] is False
    assert "autonomous_stop_scheduler_not_ready" in result["reasons"]


@pytest.mark.asyncio
async def test_already_satisfied_requires_no_physical_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(
        monkeypatch,
        safety=_safety(),
        gate=_gate(
            status="already_satisfied",
            reason="desired_state_already_observed",
            with_lease=False,
        ),
    )

    result = await preflight.async_execution_commissioning_preflight(
        _Hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=NOW,
    )

    assert result["status"] == PREFLIGHT_NO_START_NEEDED
    assert result["can_arm_to_execute"] is False
    assert result["immutable_stop_action"] is None
    assert result["service_call_performed"] is False
