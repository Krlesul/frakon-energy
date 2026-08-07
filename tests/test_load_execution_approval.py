from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_execution_approval import (
    APPROVAL_INTENT_EXECUTE_LOAD_PLAN,
    MAX_APPROVAL_TTL_SECONDS,
    VERIFY_EXPIRED,
    VERIFY_INVALID_SIGNATURE,
    VERIFY_OK,
    VERIFY_POLICY_NOT_ELIGIBLE,
    VERIFY_REPLAYED,
    VERIFY_SNAPSHOT_MISMATCH,
    VERIFY_UNKNOWN_APPROVAL,
    ApprovalAuthority,
    execution_snapshot_digest,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    EXECUTION_MODE_DISABLED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 8, 7, 18, 30, tzinfo=TZ)


def _profile(*, entity_id: str = "switch.enyaq_charging") -> LoadProfile:
    return LoadProfile(
        "ev-home",
        "Enyaq",
        PROFILE_KIND_EV,
        120,
        11.0,
        entity_id=entity_id,
    )


def _plan(*, starts_at: str = "2026-08-08T01:00:00+02:00", power_kw: float = 11.0) -> LoadPlan:
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


def _policy(*, max_power_kw: float = 11.0) -> LoadExecutionPolicy:
    return LoadExecutionPolicy(
        "ev-home",
        mode=EXECUTION_MODE_APPROVAL_REQUIRED,
        max_power_kw=max_power_kw,
        max_duration_minutes=120,
    )


def _authority() -> ApprovalAuthority:
    return ApprovalAuthority(b"a" * 32)


def test_snapshot_digest_is_deterministic_and_exact() -> None:
    profile = _profile()
    plan = _plan()
    policy = _policy()

    first = execution_snapshot_digest(profile, plan, policy)
    second = execution_snapshot_digest(profile, plan, policy)

    assert first == second
    assert first != execution_snapshot_digest(profile, replace(plan, starts_at="2026-08-08T01:15:00+02:00"), policy)
    assert first != execution_snapshot_digest(_profile(entity_id="switch.other"), plan, policy)
    assert first != execution_snapshot_digest(profile, plan, _policy(max_power_kw=12.0))


def test_issue_requires_current_policy_eligibility() -> None:
    authority = _authority()
    disabled = LoadExecutionPolicy("ev-home", mode=EXECUTION_MODE_DISABLED)

    with pytest.raises(ValueError, match="not eligible"):
        authority.issue(
            _profile(),
            _plan(),
            disabled,
            entity_available=True,
            now=NOW,
        )


def test_issue_rejects_unavailable_entity_and_excessive_ttl() -> None:
    authority = _authority()
    with pytest.raises(ValueError, match="not eligible"):
        authority.issue(
            _profile(),
            _plan(),
            _policy(),
            entity_available=False,
            now=NOW,
        )
    with pytest.raises(ValueError, match="ttl_seconds"):
        authority.issue(
            _profile(),
            _plan(),
            _policy(),
            entity_available=True,
            now=NOW,
            ttl_seconds=MAX_APPROVAL_TTL_SECONDS + 1,
        )


def test_valid_approval_verifies_without_execution() -> None:
    authority = _authority()
    approval = authority.issue(
        _profile(),
        _plan(),
        _policy(),
        entity_available=True,
        now=NOW,
        ttl_seconds=120,
    )

    verification = authority.verify(
        approval,
        _profile(),
        _plan(),
        _policy(),
        entity_available=True,
        now=NOW + timedelta(seconds=30),
    )

    assert approval.intent == APPROVAL_INTENT_EXECUTE_LOAD_PLAN
    assert verification.valid is True
    assert verification.reason == VERIFY_OK
    assert verification.consumed is False
    assert verification.execution_performed is False


@pytest.mark.parametrize(
    ("profile", "plan", "policy"),
    [
        (_profile(entity_id="switch.other"), _plan(), _policy()),
        (_profile(), _plan(starts_at="2026-08-08T01:15:00+02:00"), _policy()),
        (_profile(), _plan(), _policy(max_power_kw=12.0)),
    ],
)
def test_changed_profile_plan_or_policy_invalidates_approval(
    profile: LoadProfile,
    plan: LoadPlan,
    policy: LoadExecutionPolicy,
) -> None:
    authority = _authority()
    approval = authority.issue(
        _profile(),
        _plan(),
        _policy(),
        entity_available=True,
        now=NOW,
    )

    verification = authority.verify(
        approval,
        profile,
        plan,
        policy,
        entity_available=True,
        now=NOW + timedelta(seconds=10),
    )

    assert verification.valid is False
    assert verification.reason == VERIFY_SNAPSHOT_MISMATCH
    assert verification.execution_performed is False


def test_entity_becoming_unavailable_invalidates_policy_even_with_same_snapshot() -> None:
    authority = _authority()
    approval = authority.issue(
        _profile(),
        _plan(),
        _policy(),
        entity_available=True,
        now=NOW,
    )

    verification = authority.verify(
        approval,
        _profile(),
        _plan(),
        _policy(),
        entity_available=False,
        now=NOW + timedelta(seconds=10),
    )

    assert verification.valid is False
    assert verification.reason == VERIFY_POLICY_NOT_ELIGIBLE


def test_expired_approval_is_invalid() -> None:
    authority = _authority()
    approval = authority.issue(
        _profile(),
        _plan(),
        _policy(),
        entity_available=True,
        now=NOW,
        ttl_seconds=10,
    )

    verification = authority.verify(
        approval,
        _profile(),
        _plan(),
        _policy(),
        entity_available=True,
        now=NOW + timedelta(seconds=10),
    )

    assert verification.valid is False
    assert verification.reason == VERIFY_EXPIRED


def test_tampered_signature_is_invalid() -> None:
    authority = _authority()
    approval = authority.issue(
        _profile(),
        _plan(),
        _policy(),
        entity_available=True,
        now=NOW,
    )
    tampered = replace(approval, signature="0" * 64)

    verification = authority.verify(
        tampered,
        _profile(),
        _plan(),
        _policy(),
        entity_available=True,
        now=NOW + timedelta(seconds=10),
    )

    assert verification.valid is False
    assert verification.reason == VERIFY_INVALID_SIGNATURE


def test_consume_is_one_time_and_never_executes() -> None:
    authority = _authority()
    approval = authority.issue(
        _profile(),
        _plan(),
        _policy(),
        entity_available=True,
        now=NOW,
    )

    first = authority.consume(
        approval,
        _profile(),
        _plan(),
        _policy(),
        entity_available=True,
        now=NOW + timedelta(seconds=10),
    )
    second = authority.consume(
        approval,
        _profile(),
        _plan(),
        _policy(),
        entity_available=True,
        now=NOW + timedelta(seconds=11),
    )

    assert first.valid is True
    assert first.consumed is True
    assert first.execution_performed is False
    assert second.valid is False
    assert second.reason == VERIFY_REPLAYED
    assert second.consumed is True
    assert second.execution_performed is False


def test_new_authority_after_restart_rejects_old_approval() -> None:
    before_restart = ApprovalAuthority(b"a" * 32)
    approval = before_restart.issue(
        _profile(),
        _plan(),
        _policy(),
        entity_available=True,
        now=NOW,
    )
    after_restart = ApprovalAuthority(b"b" * 32)

    verification = after_restart.verify(
        approval,
        _profile(),
        _plan(),
        _policy(),
        entity_available=True,
        now=NOW + timedelta(seconds=10),
    )

    assert verification.valid is False
    assert verification.reason == VERIFY_UNKNOWN_APPROVAL
    assert verification.execution_performed is False


def test_naive_clock_is_rejected() -> None:
    authority = _authority()
    with pytest.raises(ValueError, match="timezone-aware"):
        authority.issue(
            _profile(),
            _plan(),
            _policy(),
            entity_available=True,
            now=datetime(2026, 8, 7, 18, 30),
        )
