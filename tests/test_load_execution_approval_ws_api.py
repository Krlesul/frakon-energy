from datetime import datetime, timedelta, timezone

import pytest

from custom_components.frakon_energy import load_execution_approval_ws_api as approval_ws
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_execution_approval import (
    APPROVAL_INTENT_EXECUTE_LOAD_PLAN,
    APPROVAL_SCHEMA_VERSION,
    MAX_APPROVAL_TTL_SECONDS,
    execution_snapshot_digest,
)
from custom_components.frakon_energy.load_execution_policy import (
    DECISION_APPROVAL_REQUIRED,
    DECISION_BLOCKED,
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile


def _profile() -> LoadProfile:
    return LoadProfile(
        "ev-home",
        "Enyaq",
        PROFILE_KIND_EV,
        120,
        11.0,
        entity_id="switch.enyaq_charging",
    )


def _policy() -> LoadExecutionPolicy:
    return LoadExecutionPolicy(
        "ev-home",
        mode=EXECUTION_MODE_APPROVAL_REQUIRED,
        max_power_kw=11.0,
        max_duration_minutes=120,
    )


def _plan() -> LoadPlan:
    return LoadPlan(
        load_id="ev-home",
        name="Enyaq",
        starts_at="2026-08-08T01:00:00+02:00",
        ends_at="2026-08-08T03:00:00+02:00",
        duration_minutes=120,
        interval_count=8,
        power_kw=11.0,
        average_czk_kwh=2.0,
        minimum_czk_kwh=1.5,
        maximum_czk_kwh=2.5,
        estimated_energy_kwh=22.0,
        estimated_cost_czk=44.0,
    )


def _eligible_evaluation() -> dict[str, object]:
    return {
        "status": DECISION_APPROVAL_REQUIRED,
        "profile_id": "ev-home",
        "entity_id": "switch.enyaq_charging",
        "reasons": [],
        "profile": _profile().as_dict(),
        "policy": _policy().as_dict(),
        "plan": {**_plan().as_dict(), "read_only": True},
        "entity_available": True,
        "execution_performed": False,
        "executor_available": False,
    }


@pytest.mark.asyncio
async def test_eligible_preview_exposes_exact_scope_without_issuing_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_evaluate(*args: object, **kwargs: object) -> dict[str, object]:
        return _eligible_evaluation()

    monkeypatch.setattr(approval_ws, "async_evaluate_profile_execution", fake_evaluate)

    result = await approval_ws.async_preview_execution_approval(
        object(),
        entry_id="entry-1",
        profile_id="ev-home",
        ttl_seconds=120,
    )

    assert result["eligible"] is True
    assert result["status"] == DECISION_APPROVAL_REQUIRED
    assert result["intent"] == APPROVAL_INTENT_EXECUTE_LOAD_PLAN
    assert result["schema_version"] == APPROVAL_SCHEMA_VERSION
    assert result["snapshot_digest"] == execution_snapshot_digest(_profile(), _plan(), _policy())
    assert result["approval_issued"] is False
    assert result["approval_id"] is None
    assert result["signature"] is None
    assert result["execution_performed"] is False
    assert result["executor_available"] is False
    assert result["preview_only"] is True


@pytest.mark.asyncio
async def test_blocked_preview_never_exposes_approval_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    blocked = _eligible_evaluation()
    blocked.update(
        {
            "status": DECISION_BLOCKED,
            "reasons": ["policy_disabled"],
        }
    )

    async def fake_evaluate(*args: object, **kwargs: object) -> dict[str, object]:
        return blocked

    monkeypatch.setattr(approval_ws, "async_evaluate_profile_execution", fake_evaluate)

    result = await approval_ws.async_preview_execution_approval(
        object(), entry_id="entry-1", profile_id="ev-home"
    )

    assert result["eligible"] is False
    assert result["snapshot_digest"] is None
    assert result["reasons"] == ["policy_disabled"]
    assert result["approval_issued"] is False
    assert result["execution_performed"] is False


@pytest.mark.asyncio
async def test_preview_passes_time_window_to_policy_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_evaluate(*args: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return _eligible_evaluation()

    monkeypatch.setattr(approval_ws, "async_evaluate_profile_execution", fake_evaluate)
    tz = timezone(timedelta(hours=2))
    earliest = datetime(2026, 8, 7, 22, 0, tzinfo=tz)
    deadline = datetime(2026, 8, 8, 6, 0, tzinfo=tz)

    await approval_ws.async_preview_execution_approval(
        object(),
        entry_id="entry-1",
        profile_id="ev-home",
        earliest_start=earliest,
        deadline=deadline,
    )

    assert captured["earliest_start"] == earliest
    assert captured["deadline"] == deadline


@pytest.mark.asyncio
async def test_preview_rejects_ttl_above_hard_limit() -> None:
    with pytest.raises(ValueError, match="ttl_seconds"):
        await approval_ws.async_preview_execution_approval(
            object(),
            entry_id="entry-1",
            profile_id="ev-home",
            ttl_seconds=MAX_APPROVAL_TTL_SECONDS + 1,
        )


def test_preview_datetime_parser_requires_timezone() -> None:
    with pytest.raises(ValueError, match="timezone offset"):
        approval_ws._parse_datetime("2026-08-08T01:00:00", "earliest_start")
