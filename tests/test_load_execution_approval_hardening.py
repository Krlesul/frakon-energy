from datetime import datetime, timedelta, timezone

from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_execution_approval import (
    VERIFY_EXPIRED,
    VERIFY_REVOKED,
    ApprovalAuthority,
)
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 8, 7, 18, 30, tzinfo=TZ)


def _profile() -> LoadProfile:
    return LoadProfile("ev-home", "Enyaq", PROFILE_KIND_EV, 120, 11.0, entity_id="switch.enyaq_charging")


def _plan(*, starts_at: str = "2026-08-07T18:32:00+02:00") -> LoadPlan:
    return LoadPlan(
        load_id="ev-home",
        name="Enyaq",
        starts_at=starts_at,
        ends_at="2026-08-07T20:32:00+02:00",
        duration_minutes=120,
        interval_count=8,
        power_kw=11.0,
        average_czk_kwh=2.0,
        minimum_czk_kwh=1.5,
        maximum_czk_kwh=2.5,
        estimated_energy_kwh=22.0,
        estimated_cost_czk=44.0,
    )


def _policy() -> LoadExecutionPolicy:
    return LoadExecutionPolicy(
        "ev-home",
        mode=EXECUTION_MODE_APPROVAL_REQUIRED,
        max_power_kw=11.0,
        max_duration_minutes=120,
    )


def test_approval_expiry_is_capped_at_plan_start() -> None:
    authority = ApprovalAuthority(b"a" * 32)
    approval = authority.issue(
        _profile(),
        _plan(),
        _policy(),
        entity_available=True,
        now=NOW,
        ttl_seconds=300,
    )

    expected_plan_start = int(datetime.fromisoformat(_plan().starts_at).timestamp())
    assert approval.expires_at == expected_plan_start

    verification = authority.verify(
        approval,
        _profile(),
        _plan(),
        _policy(),
        entity_available=True,
        now=datetime.fromisoformat(_plan().starts_at),
    )
    assert verification.valid is False
    assert verification.reason == VERIFY_EXPIRED


def test_known_approval_can_be_revoked_and_never_verified_again() -> None:
    authority = ApprovalAuthority(b"a" * 32)
    approval = authority.issue(
        _profile(),
        _plan(starts_at="2026-08-07T19:00:00+02:00"),
        _policy(),
        entity_available=True,
        now=NOW,
    )

    revoked = authority.revoke(approval)
    assert revoked.valid is False
    assert revoked.reason == VERIFY_REVOKED
    assert revoked.execution_performed is False

    verification = authority.verify(
        approval,
        _profile(),
        _plan(starts_at="2026-08-07T19:00:00+02:00"),
        _policy(),
        entity_available=True,
        now=NOW + timedelta(seconds=10),
    )
    assert verification.valid is False
    assert verification.reason == VERIFY_REVOKED
    assert verification.execution_performed is False
