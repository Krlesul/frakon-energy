from datetime import datetime, timedelta, timezone

import pytest

from custom_components.frakon_energy import load_execution_approval_ws_api as approval_ws
from custom_components.frakon_energy.load_execution_approval import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_REVOKED,
    VALIDATION_SCOPE_CHANGED,
)

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 8, 7, 18, 30, tzinfo=TZ)
START = datetime(2026, 8, 7, 20, 0, tzinfo=TZ)
END = datetime(2026, 8, 7, 22, 0, tzinfo=TZ)


class FakeHass:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}


def _evaluation(*, status: str = "approval_required", power_kw: float = 11.0) -> dict[str, object]:
    reasons = [] if status == "approval_required" else ["policy_disabled"]
    return {
        "status": status,
        "profile_id": "ev-home",
        "entity_id": "switch.ev_charging",
        "reasons": reasons,
        "plan": {
            "starts_at": START.isoformat(),
            "ends_at": END.isoformat(),
            "power_kw": power_kw,
            "duration_minutes": 120,
            "average_czk_kwh": 2.0,
            "estimated_cost_czk": 44.0,
        },
        "policy": {
            "profile_id": "ev-home",
            "mode": "approval_required" if status == "approval_required" else "disabled",
            "max_power_kw": 11.0 if status == "approval_required" else None,
            "max_duration_minutes": 120 if status == "approval_required" else None,
            "require_entity_binding": True,
            "require_entity_available": True,
        },
        "execution_performed": False,
        "executor_available": False,
    }


def _patch_evaluation(monkeypatch: pytest.MonkeyPatch, value: dict[str, object]) -> None:
    async def fake_evaluate(*args: object, **kwargs: object) -> dict[str, object]:
        return value

    monkeypatch.setattr(approval_ws, "async_evaluate_profile_execution", fake_evaluate)


@pytest.mark.asyncio
async def test_request_blocked_policy_does_not_create_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = FakeHass()
    _patch_evaluation(monkeypatch, _evaluation(status="blocked"))

    result = await approval_ws.async_request_approval(
        hass,
        entry_id="entry-1",
        profile_id="ev-home",
        now=NOW,
    )

    assert result["created"] is False
    assert result["approval"] is None
    assert result["execution_performed"] is False
    assert result["executor_available"] is False
    assert result["can_execute"] is False
    assert approval_ws._registry(hass).list() == ()


@pytest.mark.asyncio
async def test_request_clean_policy_creates_runtime_pending_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = FakeHass()
    _patch_evaluation(monkeypatch, _evaluation())

    result = await approval_ws.async_request_approval(
        hass,
        entry_id="entry-1",
        profile_id="ev-home",
        ttl_seconds=300,
        now=NOW,
    )

    assert result["created"] is True
    approval = result["approval"]
    assert approval["status"] == APPROVAL_STATUS_PENDING
    assert approval["runtime_only"] is True
    assert approval["survives_restart"] is False
    assert approval["can_execute"] is False
    assert len(approval_ws._registry(hass).list(entry_id="entry-1")) == 1


@pytest.mark.asyncio
async def test_approve_rechecks_exact_scope_and_records_user(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = FakeHass()
    _patch_evaluation(monkeypatch, _evaluation())
    requested = await approval_ws.async_request_approval(
        hass, entry_id="entry-1", profile_id="ev-home", now=NOW
    )
    approval_id = requested["approval"]["approval_id"]

    result = await approval_ws.async_approve_request(
        hass,
        entry_id="entry-1",
        approval_id=approval_id,
        approved_by="user-123",
        now=NOW + timedelta(seconds=10),
    )

    assert result["approval"]["status"] == APPROVAL_STATUS_APPROVED
    assert result["approval"]["approved_by"] == "user-123"
    assert result["can_execute"] is False
    assert result["execution_performed"] is False


@pytest.mark.asyncio
async def test_approve_fails_if_scope_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = FakeHass()
    _patch_evaluation(monkeypatch, _evaluation())
    requested = await approval_ws.async_request_approval(
        hass, entry_id="entry-1", profile_id="ev-home", now=NOW
    )
    approval_id = requested["approval"]["approval_id"]

    _patch_evaluation(monkeypatch, _evaluation(power_kw=7.2))
    with pytest.raises(ValueError, match="scope changed"):
        await approval_ws.async_approve_request(
            hass,
            entry_id="entry-1",
            approval_id=approval_id,
            approved_by="user-123",
            now=NOW + timedelta(seconds=10),
        )


@pytest.mark.asyncio
async def test_validate_detects_fresh_scope_change_without_consuming(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = FakeHass()
    _patch_evaluation(monkeypatch, _evaluation())
    requested = await approval_ws.async_request_approval(
        hass, entry_id="entry-1", profile_id="ev-home", now=NOW
    )
    approval_id = requested["approval"]["approval_id"]
    await approval_ws.async_approve_request(
        hass,
        entry_id="entry-1",
        approval_id=approval_id,
        approved_by="user-123",
        now=NOW + timedelta(seconds=10),
    )

    _patch_evaluation(monkeypatch, _evaluation(power_kw=7.2))
    result = await approval_ws.async_validate_request(
        hass,
        entry_id="entry-1",
        approval_id=approval_id,
        now=NOW + timedelta(seconds=20),
    )

    assert result["validation"]["valid"] is False
    assert VALIDATION_SCOPE_CHANGED in result["validation"]["reasons"]
    assert result["approval"]["status"] == APPROVAL_STATUS_APPROVED
    assert result["execution_performed"] is False


@pytest.mark.asyncio
async def test_revoke_and_entry_scoping(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = FakeHass()
    _patch_evaluation(monkeypatch, _evaluation())
    requested = await approval_ws.async_request_approval(
        hass, entry_id="entry-1", profile_id="ev-home", now=NOW
    )
    approval_id = requested["approval"]["approval_id"]
    registry = approval_ws._registry(hass)

    with pytest.raises(ValueError, match="does not belong"):
        approval_ws._approval_for_entry(registry, approval_id=approval_id, entry_id="entry-2")

    updated = registry.revoke(approval_id, now=NOW + timedelta(seconds=5))
    assert updated.status == APPROVAL_STATUS_REVOKED
    listed = approval_ws._list_payload(registry, "entry-1")
    assert len(listed["approvals"]) == 1
    assert listed["approvals"][0]["status"] == APPROVAL_STATUS_REVOKED
    assert listed["can_execute"] is False
