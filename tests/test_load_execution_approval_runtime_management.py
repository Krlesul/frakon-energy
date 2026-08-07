from datetime import datetime, timedelta, timezone

import pytest

from custom_components.frakon_energy import load_execution_approval_ws_api as approval_ws
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_execution_approval import execution_snapshot_digest
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
async def test_issue_creates_entry_scoped_runtime_record(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = FakeHass()
    _patch_evaluation(monkeypatch, _evaluation())
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

    listed = approval_ws._list_payload(hass, "entry-1")
    assert len(listed["approvals"]) == 1
    record = listed["approvals"][0]
    assert record["approved_by"] == "admin-1"
    assert record["profile_id"] == "ev-home"
    assert record["status"] == "approved"
    assert record["can_execute"] is False
    assert listed["approvals"] != approval_ws._list_payload(hass, "entry-2")["approvals"]


@pytest.mark.asyncio
async def test_issue_ttl_is_capped_at_plan_start(monkeypatch: pytest.MonkeyPatch) -> None:
    close_plan = _plan(starts_at="2026-08-07T18:32:00+02:00")
    hass = FakeHass()
    _patch_evaluation(monkeypatch, _evaluation(plan=close_plan))
    digest = execution_snapshot_digest(_profile(), close_plan, _policy())

    result = await approval_ws.async_issue_execution_approval(
        hass,
        entry_id="entry-1",
        profile_id="ev-home",
        expected_snapshot_digest=digest,
        ttl_seconds=300,
        now=NOW,
    )

    assert result["ttl_seconds"] == 120
    assert result["expires_at"] == int(datetime.fromisoformat(close_plan.starts_at).timestamp())


@pytest.mark.asyncio
async def test_verify_recalculates_exact_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
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

    verified = await approval_ws.async_verify_execution_approval(
        hass,
        entry_id="entry-1",
        approval_id=issued["approval_id"],
        now=NOW + timedelta(seconds=10),
    )
    assert verified["verification"]["valid"] is True
    assert verified["verification"]["reason"] == "ok"
    assert verified["execution_performed"] is False
    assert verified["can_execute"] is False

    _patch_evaluation(monkeypatch, _evaluation(plan=_plan(power_kw=7.2)))
    changed = await approval_ws.async_verify_execution_approval(
        hass,
        entry_id="entry-1",
        approval_id=issued["approval_id"],
        now=NOW + timedelta(seconds=20),
    )
    assert changed["verification"]["valid"] is False
    assert changed["verification"]["reason"] == "snapshot_mismatch"


@pytest.mark.asyncio
async def test_revoke_is_terminal_for_runtime_verify(monkeypatch: pytest.MonkeyPatch) -> None:
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

    revoked = approval_ws.async_revoke_execution_approval(
        hass,
        entry_id="entry-1",
        approval_id=issued["approval_id"],
    )
    assert revoked["record"]["status"] == "revoked"
    assert revoked["verification"]["reason"] == "revoked"
    assert revoked["execution_performed"] is False

    verified = await approval_ws.async_verify_execution_approval(
        hass,
        entry_id="entry-1",
        approval_id=issued["approval_id"],
        now=NOW + timedelta(seconds=10),
    )
    assert verified["verification"]["valid"] is False
    assert verified["verification"]["reason"] == "revoked"


def test_new_hass_runtime_forgets_approval_records() -> None:
    first = FakeHass()
    second = FakeHass()
    assert approval_ws._approval_records(first, "entry-1") == {}
    assert approval_ws._approval_records(second, "entry-1") == {}
    assert first.data is not second.data
