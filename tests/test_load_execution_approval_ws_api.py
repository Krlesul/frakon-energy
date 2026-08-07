from datetime import datetime, timedelta, timezone

import pytest

from custom_components.frakon_energy import load_execution_approval_ws_api as approval_ws
from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_execution_approval import (
    APPROVAL_INTENT_EXECUTE_LOAD_PLAN,
    APPROVAL_SCHEMA_VERSION,
    MAX_APPROVAL_TTL_SECONDS,
    VERIFY_OK,
    VERIFY_UNKNOWN_APPROVAL,
    ExecutionApproval,
    execution_snapshot_digest,
)
from custom_components.frakon_energy.load_execution_policy import (
    DECISION_APPROVAL_REQUIRED,
    DECISION_BLOCKED,
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 8, 7, 18, 30, tzinfo=TZ)


class _FakeHass:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}


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
    earliest = datetime(2026, 8, 7, 22, 0, tzinfo=TZ)
    deadline = datetime(2026, 8, 8, 6, 0, tzinfo=TZ)

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


def test_expected_digest_must_be_lowercase_sha256() -> None:
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        approval_ws._validate_expected_digest("ABC")


@pytest.mark.asyncio
async def test_exact_fresh_digest_issues_signed_approval_without_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_evaluate(*args: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return _eligible_evaluation()

    monkeypatch.setattr(approval_ws, "async_evaluate_profile_execution", fake_evaluate)
    hass = _FakeHass()
    digest = execution_snapshot_digest(_profile(), _plan(), _policy())

    result = await approval_ws.async_issue_execution_approval(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        profile_id="ev-home",
        expected_snapshot_digest=digest,
        ttl_seconds=120,
        now=NOW,
    )

    assert captured["now"] == NOW
    assert result["approval_issued"] is True
    assert result["snapshot_digest"] == digest
    assert result["expected_snapshot_digest"] == digest
    assert result["approval_id"]
    assert result["signature"]
    assert result["ttl_seconds"] == 120
    assert result["execution_performed"] is False
    assert result["executor_available"] is False
    assert result["consumed"] is False

    approval = ExecutionApproval(**result["approval"])  # type: ignore[arg-type]
    verification = approval_ws._approval_authority(hass).verify(  # type: ignore[arg-type]
        approval,
        _profile(),
        _plan(),
        _policy(),
        entity_available=True,
        now=NOW + timedelta(seconds=1),
    )
    assert verification.valid is True
    assert verification.reason == VERIFY_OK
    assert verification.execution_performed is False


@pytest.mark.asyncio
async def test_changed_scope_digest_is_rejected_before_issuance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_evaluate(*args: object, **kwargs: object) -> dict[str, object]:
        return _eligible_evaluation()

    monkeypatch.setattr(approval_ws, "async_evaluate_profile_execution", fake_evaluate)
    hass = _FakeHass()
    stale_digest = "0" * 64

    with pytest.raises(approval_ws.ApprovalScopeChangedError, match="scope changed"):
        await approval_ws.async_issue_execution_approval(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            profile_id="ev-home",
            expected_snapshot_digest=stale_digest,
            now=NOW,
        )

    assert approval_ws._AUTHORITY_KEY not in hass.data.get("frakon_energy", {})  # type: ignore[operator]


@pytest.mark.asyncio
async def test_blocked_candidate_cannot_issue_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    blocked = _eligible_evaluation()
    blocked.update({"status": DECISION_BLOCKED, "reasons": ["policy_disabled"]})

    async def fake_evaluate(*args: object, **kwargs: object) -> dict[str, object]:
        return blocked

    monkeypatch.setattr(approval_ws, "async_evaluate_profile_execution", fake_evaluate)
    hass = _FakeHass()

    with pytest.raises(ValueError, match="not eligible"):
        await approval_ws.async_issue_execution_approval(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            profile_id="ev-home",
            expected_snapshot_digest=execution_snapshot_digest(_profile(), _plan(), _policy()),
            now=NOW,
        )


@pytest.mark.asyncio
async def test_issue_enforces_ttl_hard_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_evaluate(*args: object, **kwargs: object) -> dict[str, object]:
        return _eligible_evaluation()

    monkeypatch.setattr(approval_ws, "async_evaluate_profile_execution", fake_evaluate)

    with pytest.raises(ValueError, match="ttl_seconds"):
        await approval_ws.async_issue_execution_approval(
            _FakeHass(),  # type: ignore[arg-type]
            entry_id="entry-1",
            profile_id="ev-home",
            expected_snapshot_digest=execution_snapshot_digest(_profile(), _plan(), _policy()),
            ttl_seconds=MAX_APPROVAL_TTL_SECONDS + 1,
            now=NOW,
        )


def test_authority_is_reused_within_process_and_replaced_after_restart() -> None:
    first_hass = _FakeHass()
    second_hass = _FakeHass()

    first = approval_ws._approval_authority(first_hass)  # type: ignore[arg-type]
    again = approval_ws._approval_authority(first_hass)  # type: ignore[arg-type]
    after_restart = approval_ws._approval_authority(second_hass)  # type: ignore[arg-type]

    assert first is again
    assert first is not after_restart


@pytest.mark.asyncio
async def test_new_process_authority_rejects_pre_restart_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_evaluate(*args: object, **kwargs: object) -> dict[str, object]:
        return _eligible_evaluation()

    monkeypatch.setattr(approval_ws, "async_evaluate_profile_execution", fake_evaluate)
    before_restart = _FakeHass()
    digest = execution_snapshot_digest(_profile(), _plan(), _policy())
    issued = await approval_ws.async_issue_execution_approval(
        before_restart,  # type: ignore[arg-type]
        entry_id="entry-1",
        profile_id="ev-home",
        expected_snapshot_digest=digest,
        now=NOW,
    )
    approval = ExecutionApproval(**issued["approval"])  # type: ignore[arg-type]

    after_restart = _FakeHass()
    verification = approval_ws._approval_authority(after_restart).verify(  # type: ignore[arg-type]
        approval,
        _profile(),
        _plan(),
        _policy(),
        entity_available=True,
        now=NOW + timedelta(seconds=1),
    )

    assert verification.valid is False
    assert verification.reason == VERIFY_UNKNOWN_APPROVAL
    assert verification.execution_performed is False
