from datetime import datetime, timedelta, timezone

import pytest

from custom_components.frakon_energy import load_execution_approval_ws_api as approval_ws
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_execution_approval import (
    APPROVAL_INTENT_EXECUTE_LOAD_PLAN,
    VERIFY_OK,
    VERIFY_REVOKED,
    execution_snapshot_digest,
)
from custom_components.frakon_energy.load_execution_policy import (
    DECISION_APPROVAL_REQUIRED,
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 8, 7, 18, 30, tzinfo=TZ)


class FakeHass:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}


def _profile() -> LoadProfile:
    return LoadProfile("ev-home", "Enyaq", PROFILE_KIND_EV, 120, 11.0, entity_id="switch.enyaq_charging")


def _policy() -> LoadExecutionPolicy:
    return LoadExecutionPolicy(
        "ev-home",
        mode=EXECUTION_MODE_APPROVAL_REQUIRED,
        max_power_kw=11.0,
        max_duration_minutes=120,
    )


def _plan(*, power_kw: float = 11.0) -> LoadPlan:
    return LoadPlan(
        load_id="ev-home",
        name="Enyaq",
        starts_at="2026-08-08T01:00:00+02:00",
        ends_at="2026-08-08T03:00:00+02:00",
        duration_minutes=120,
        interval_count=8,
        power_kw=power_kw,
        average_czk_kwh=2.0,
        minimum_czk_kwh=1.5,
        maximum_czk_kwh=2.5,
        estimated_energy_kwh=22.0,
        estimated_cost_czk=44.0,
    )


def _evaluation(*, plan: LoadPlan | None = None) -> dict[str, object]:
    current_plan = plan or _plan()
    return {
        "status": DECISION_APPROVAL_REQUIRED,
        "profile_id": "ev-home",
        "entity_id": "switch.enyaq_charging",
        "reasons": [],
        "profile": _profile().as_dict(),
        "policy": _policy().as_dict(),
        "plan": {**current_plan.as_dict(), "read_only": True},
        "entity_available": True,
        "execution_performed": False,
        "executor_available": False,
    }


def _preview(*, plan: LoadPlan | None = None) -> dict[str, object]:
    current_plan = plan or _plan()
    return {
        "eligible": True,
        "status": DECISION_APPROVAL_REQUIRED,
        "reasons": [],
        "intent": APPROVAL_INTENT_EXECUTE_LOAD_PLAN,
        "schema_version": 1,
        "snapshot_digest": execution_snapshot_digest(_profile(), current_plan, _policy()),
        "profile": _profile().as_dict(),
        "policy": _policy().as_dict(),
        "plan": {**current_plan.as_dict(), "read_only": True},
        "entity_id": "switch.enyaq_charging",
        "entity_available": True,
        "ttl_seconds": 120,
        "max_ttl_seconds": 300,
        "approval_issued": False,
        "approval_id": None,
        "signature": None,
        "execution_performed": False,
        "executor_available": False,
        "preview_only": True,
        "can_execute": False,
    }


def _patch_preview(monkeypatch: pytest.MonkeyPatch, value: dict[str, object]) -> None:
    async def fake_preview(*args: object, **kwargs: object) -> dict[str, object]:
        return value

    monkeypatch.setattr(approval_ws, "async_preview_execution_approval", fake_preview)


def _patch_evaluation(monkeypatch: pytest.MonkeyPatch, value: dict[str, object]) -> None:
    async def fake_evaluate(*args: object, **kwargs: object) -> dict[str, object]:
        return value

    monkeypatch.setattr(approval_ws, "async_evaluate_profile_execution", fake_evaluate)


@pytest.mark.asyncio
async def test_issue_requires_exact_preview_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = FakeHass()
    _patch_preview(monkeypatch, _preview())

    with pytest.raises(ValueError, match="snapshot changed"):
        await approval_ws.async_issue_execution_approval(
            hass,
            entry_id="entry-1",
            profile_id="ev-home",
            expected_snapshot_digest="0" * 64,
            approved_by="user-1",
            now=NOW,
        )

    assert approval_ws._runtime(hass).records == {}


@pytest.mark.asyncio
async def test_explicit_issue_returns_signed_artifact_but_no_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = FakeHass()
    preview = _preview()
    _patch_preview(monkeypatch, preview)

    result = await approval_ws.async_issue_execution_approval(
        hass,
        entry_id="entry-1",
        profile_id="ev-home",
        expected_snapshot_digest=str(preview["snapshot_digest"]),
        approved_by="user-1",
        ttl_seconds=120,
        now=NOW,
    )

    assert result["issued"] is True
    assert result["execution_performed"] is False
    assert result["executor_available"] is False
    assert result["can_execute"] is False
    record = result["record"]
    assert record["approved_by"] == "user-1"
    assert record["status"] == "approved"
    assert record["approval"]["signature"]

    listed = approval_ws._list_payload(approval_ws._runtime(hass), "entry-1")
    assert len(listed["approvals"]) == 1
    assert "signature" not in listed["approvals"][0]["approval"]
    assert listed["can_execute"] is False


@pytest.mark.asyncio
async def test_verify_rechecks_current_candidate_and_revoke_invalidates(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = FakeHass()
    preview = _preview()
    _patch_preview(monkeypatch, preview)
    issued = await approval_ws.async_issue_execution_approval(
        hass,
        entry_id="entry-1",
        profile_id="ev-home",
        expected_snapshot_digest=str(preview["snapshot_digest"]),
        approved_by="user-1",
        now=NOW,
    )
    approval_id = issued["record"]["approval"]["approval_id"]
    _patch_evaluation(monkeypatch, _evaluation())

    verified = await approval_ws.async_verify_execution_approval(
        hass,
        entry_id="entry-1",
        approval_id=approval_id,
        now=NOW + timedelta(seconds=10),
    )
    assert verified["verification"]["valid"] is True
    assert verified["verification"]["reason"] == VERIFY_OK
    assert verified["execution_performed"] is False

    revoked = approval_ws.async_revoke_execution_approval(
        hass,
        entry_id="entry-1",
        approval_id=approval_id,
    )
    assert revoked["verification"]["reason"] == VERIFY_REVOKED
    assert revoked["record"]["status"] == "revoked"

    verified_after_revoke = await approval_ws.async_verify_execution_approval(
        hass,
        entry_id="entry-1",
        approval_id=approval_id,
        now=NOW + timedelta(seconds=20),
    )
    assert verified_after_revoke["verification"]["valid"] is False
    assert verified_after_revoke["verification"]["reason"] == VERIFY_REVOKED


@pytest.mark.asyncio
async def test_verification_detects_changed_plan_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = FakeHass()
    preview = _preview()
    _patch_preview(monkeypatch, preview)
    issued = await approval_ws.async_issue_execution_approval(
        hass,
        entry_id="entry-1",
        profile_id="ev-home",
        expected_snapshot_digest=str(preview["snapshot_digest"]),
        approved_by="user-1",
        now=NOW,
    )
    approval_id = issued["record"]["approval"]["approval_id"]
    _patch_evaluation(monkeypatch, _evaluation(plan=_plan(power_kw=7.2)))

    result = await approval_ws.async_verify_execution_approval(
        hass,
        entry_id="entry-1",
        approval_id=approval_id,
        now=NOW + timedelta(seconds=10),
    )

    assert result["verification"]["valid"] is False
    assert result["verification"]["reason"] == "snapshot_mismatch"
    assert result["execution_performed"] is False


@pytest.mark.asyncio
async def test_runtime_restart_forgets_issued_approvals(monkeypatch: pytest.MonkeyPatch) -> None:
    before = FakeHass()
    preview = _preview()
    _patch_preview(monkeypatch, preview)
    issued = await approval_ws.async_issue_execution_approval(
        before,
        entry_id="entry-1",
        profile_id="ev-home",
        expected_snapshot_digest=str(preview["snapshot_digest"]),
        approved_by="user-1",
        now=NOW,
    )
    approval_id = issued["record"]["approval"]["approval_id"]

    after_restart = FakeHass()
    with pytest.raises(ValueError, match="approval not found"):
        approval_ws._runtime(after_restart).get(approval_id, entry_id="entry-1")
