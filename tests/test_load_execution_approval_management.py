from datetime import datetime, timedelta, timezone

import pytest

from custom_components.frakon_energy import load_execution_approval_ws_api as approval_ws
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_execution_approval import (
    VERIFY_EXPIRED,
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


def _plan(*, power_kw: float = 11.0, starts_at: str = "2026-08-08T01:00:00+02:00") -> LoadPlan:
    return LoadPlan(
        load_id="ev-home",
        name="Enyaq",
        starts_at=starts_at,
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


def _patch_evaluation(monkeypatch: pytest.MonkeyPatch, value: dict[str, object]) -> None:
    async def fake_evaluate(*args: object, **kwargs: object) -> dict[str, object]:
        return value

    monkeypatch.setattr(approval_ws, "async_evaluate_profile_execution", fake_evaluate)


@pytest.mark.asyncio
async def test_issue_records_runtime_evidence_and_list_hides_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = FakeHass()
    evaluation = _evaluation()
    _patch_evaluation(monkeypatch, evaluation)
    digest = execution_snapshot_digest(_profile(), _plan(), _policy())

    result = await approval_ws.async_issue_execution_approval(
        hass,
        entry_id="entry-1",
        profile_id="ev-home",
        expected_snapshot_digest=digest,
        approved_by="admin-1",
        now=NOW,
    )

    assert result["approval_issued"] is True
    assert result["approved_by"] == "admin-1"
    assert result["can_execute"] is False
    assert result["execution_performed"] is False
    assert result["signature"]

    listed = approval_ws._list_payload(hass, "entry-1")
    assert len(listed["approvals"]) == 1
    record = listed["approvals"][0]
    assert record["approved_by"] == "admin-1"
    assert record["status"] == "approved"
    assert "signature" not in record["approval"]
    assert listed["can_execute"] is False


@pytest.mark.asyncio
async def test_verify_rechecks_snapshot_and_revoke_is_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = FakeHass()
    _patch_evaluation(monkeypatch, _evaluation())
    digest = execution_snapshot_digest(_profile(), _plan(), _policy())
    issued = await approval_ws.async_issue_execution_approval(
        hass,
        entry_id="entry-1",
        profile_id="ev-home",
        expected_snapshot_digest=digest,
        approved_by="admin-1",
        now=NOW,
    )
    approval_id = issued["approval_id"]

    verified = await approval_ws.async_verify_execution_approval(
        hass,
        entry_id="entry-1",
        approval_id=approval_id,
        now=NOW + timedelta(seconds=10),
    )
    assert verified["verification"]["valid"] is True
    assert verified["verification"]["reason"] == VERIFY_OK
    assert verified["execution_performed"] is False

    revoked = approval_ws.async_revoke_execution_approval(hass, entry_id="entry-1", approval_id=approval_id)
    assert revoked["verification"]["reason"] == VERIFY_REVOKED
    assert revoked["record"]["status"] == "revoked"

    verified_after = await approval_ws.async_verify_execution_approval(
        hass,
        entry_id="entry-1",
        approval_id=approval_id,
        now=NOW + timedelta(seconds=20),
    )
    assert verified_after["verification"]["valid"] is False
    assert verified_after["verification"]["reason"] == VERIFY_REVOKED


@pytest.mark.asyncio
async def test_verify_detects_fresh_snapshot_change(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = FakeHass()
    _patch_evaluation(monkeypatch, _evaluation())
    digest = execution_snapshot_digest(_profile(), _plan(), _policy())
    issued = await approval_ws.async_issue_execution_approval(
        hass,
        entry_id="entry-1",
        profile_id="ev-home",
        expected_snapshot_digest=digest,
        approved_by="admin-1",
        now=NOW,
    )

    _patch_evaluation(monkeypatch, _evaluation(plan=_plan(power_kw=7.2)))
    verified = await approval_ws.async_verify_execution_approval(
        hass,
        entry_id="entry-1",
        approval_id=issued["approval_id"],
        now=NOW + timedelta(seconds=10),
    )
    assert verified["verification"]["valid"] is False
    assert verified["verification"]["reason"] == "snapshot_mismatch"


def test_authority_caps_expiry_at_plan_start_and_supports_revoke() -> None:
    authority = approval_ws.ApprovalAuthority(b"a" * 32)
    close_plan = _plan(starts_at="2026-08-07T18:32:00+02:00")
    approval = authority.issue(
        _profile(), close_plan, _policy(), entity_available=True, now=NOW, ttl_seconds=300
    )
    assert approval.expires_at == int(datetime.fromisoformat(close_plan.starts_at).timestamp())
    at_start = authority.verify(
        approval,
        _profile(),
        close_plan,
        _policy(),
        entity_available=True,
        now=datetime.fromisoformat(close_plan.starts_at),
    )
    assert at_start.valid is False
    assert at_start.reason == VERIFY_EXPIRED

    later_plan = _plan(starts_at="2026-08-07T19:00:00+02:00")
    approval2 = authority.issue(
        _profile(), later_plan, _policy(), entity_available=True, now=NOW
    )
    revoked = authority.revoke(approval2)
    assert revoked.reason == VERIFY_REVOKED
    assert revoked.execution_performed is False


def test_runtime_records_are_entry_scoped_and_restart_fail_closed() -> None:
    first = FakeHass()
    second = FakeHass()
    assert approval_ws._approval_records(first, "entry-1") == {}
    assert approval_ws._approval_records(first, "entry-2") == {}
    assert approval_ws._approval_records(second, "entry-1") == {}
