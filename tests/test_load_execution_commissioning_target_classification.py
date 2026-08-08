from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_commissioning_preflight as preflight
from custom_components.frakon_energy.load_execution_commissioning_preflight import (
    COMMISSIONING_TARGET_HELPER,
    COMMISSIONING_TARGET_PHYSICAL_CAPABLE,
    PREFLIGHT_READY_FOR_ARM,
)

NOW = datetime(2026, 8, 8, 15, 30, tzinfo=timezone.utc)


class _Guard:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Hass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}


def _safety() -> dict[str, Any]:
    return {
        "execution_arm": {"armed": False, "storage_healthy": True},
        "start_recovery": {"status": "ok"},
        "stop_recovery": {"status": "ok"},
        "start_scheduler": {"started": True, "healthy": True},
        "stop_scheduler": {"started": True, "healthy": True},
    }


def _gate(entity_id: str, domain: str) -> dict[str, Any]:
    return {
        "lifecycle": {
            "lifecycle_id": "life-1",
            "attempt_id": "attempt-1",
            "entity_id": entity_id,
            "service_domain": domain,
            "service_name": "turn_on",
        },
        "stop_lease": {
            "lease_id": "lease-1",
            "entity_id": entity_id,
            "service_domain": domain,
            "service_name": "turn_off",
            "ends_at": "2026-08-08T17:00:00+00:00",
        },
        "bounded_dispatch_gate": {
            "status": "ready_to_start",
            "reason": "bounded_start_has_armed_stop_obligation",
            "stop_lease_matches": True,
            "dispatch_gate_matches": True,
            "can_start": True,
        },
    }


def _wire(monkeypatch: pytest.MonkeyPatch, *, entity_id: str, domain: str) -> None:
    monkeypatch.setattr(preflight, "execution_arm_guard", lambda hass, entry_id: _Guard())

    async def safety_status(hass, *, entry_id):
        return _safety()

    async def bounded_gate(hass, *, entry_id, attempt_id, now):
        return _gate(entity_id, domain)

    monkeypatch.setattr(preflight, "async_execution_safety_status", safety_status)
    monkeypatch.setattr(preflight, "async_bounded_dispatch_gate", bounded_gate)


@pytest.mark.asyncio
async def test_input_boolean_is_reported_as_direct_software_helper_not_proven_side_effect_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(
        monkeypatch,
        entity_id="input_boolean.frakon_execution_test",
        domain="input_boolean",
    )

    result = await preflight.async_execution_commissioning_preflight(
        _Hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=NOW,
    )

    target = result["commissioning_target"]
    assert result["status"] == PREFLIGHT_READY_FOR_ARM
    assert target["class"] == COMMISSIONING_TARGET_HELPER
    assert target["home_assistant_helper"] is True
    assert target["direct_hardware_service"] is False
    assert target["recommended_first_field_test_target"] is True
    assert target["requires_downstream_automation_review"] is True
    assert target["indirect_automation_side_effects_assessed"] is False
    assert result["immutable_start_action"]["service_domain"] == "input_boolean"
    assert result["immutable_stop_action"]["service_domain"] == "input_boolean"
    assert result["service_call_performed"] is False
    assert result["execution_performed"] is False


@pytest.mark.asyncio
async def test_switch_remains_physical_capable_commissioning_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(
        monkeypatch,
        entity_id="switch.enyaq_charging",
        domain="switch",
    )

    result = await preflight.async_execution_commissioning_preflight(
        _Hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id="attempt-1",
        now=NOW,
    )

    target = result["commissioning_target"]
    assert target["class"] == COMMISSIONING_TARGET_PHYSICAL_CAPABLE
    assert target["home_assistant_helper"] is False
    assert target["direct_hardware_service"] is True
    assert target["recommended_first_field_test_target"] is False
    assert target["requires_downstream_automation_review"] is False
    assert target["indirect_automation_side_effects_assessed"] is False
    assert result["service_call_performed"] is False
