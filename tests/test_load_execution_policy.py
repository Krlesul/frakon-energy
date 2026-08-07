import pytest

from custom_components.frakon_energy.energy_load_planner import LoadPlan
from custom_components.frakon_energy.load_execution_policy import (
    DECISION_APPROVAL_REQUIRED,
    DECISION_BLOCKED,
    EXECUTION_MODE_APPROVAL_REQUIRED,
    EXECUTION_MODE_DISABLED,
    REASON_DURATION_LIMIT_EXCEEDED,
    REASON_ENTITY_BINDING_REQUIRED,
    REASON_ENTITY_UNAVAILABLE,
    REASON_PLAN_PROFILE_MISMATCH,
    REASON_POLICY_DISABLED,
    REASON_POWER_LIMIT_EXCEEDED,
    REASON_PROFILE_DISABLED,
    LoadExecutionPolicy,
    evaluate_execution_policy,
)
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile


def _profile(*, enabled: bool = True, entity_id: str | None = "switch.enyaq_charging") -> LoadProfile:
    return LoadProfile(
        "ev-home",
        "Enyaq",
        PROFILE_KIND_EV,
        120,
        11.0,
        enabled=enabled,
        entity_id=entity_id,
    )


def _plan(*, load_id: str = "ev-home", power_kw: float = 11.0, duration_minutes: int = 120) -> LoadPlan:
    return LoadPlan(
        load_id=load_id,
        name="Enyaq",
        starts_at="2026-08-08T01:00:00+02:00",
        ends_at="2026-08-08T03:00:00+02:00",
        duration_minutes=duration_minutes,
        interval_count=duration_minutes // 15,
        power_kw=power_kw,
        average_czk_kwh=2.0,
        minimum_czk_kwh=1.5,
        maximum_czk_kwh=2.5,
        estimated_energy_kwh=power_kw * duration_minutes / 60,
        estimated_cost_czk=power_kw * duration_minutes / 60 * 2.0,
    )


def _approval_policy(**overrides: object) -> LoadExecutionPolicy:
    values: dict[str, object] = {
        "profile_id": "ev-home",
        "mode": EXECUTION_MODE_APPROVAL_REQUIRED,
        "max_power_kw": 11.0,
        "max_duration_minutes": 120,
    }
    values.update(overrides)
    return LoadExecutionPolicy(**values)  # type: ignore[arg-type]


def test_disabled_policy_always_blocks() -> None:
    decision = evaluate_execution_policy(
        _profile(),
        _plan(),
        LoadExecutionPolicy("ev-home", mode=EXECUTION_MODE_DISABLED),
        entity_available=True,
    )

    assert decision.status == DECISION_BLOCKED
    assert REASON_POLICY_DISABLED in decision.reasons
    assert decision.execution_performed is False


def test_clean_policy_can_only_require_approval() -> None:
    decision = evaluate_execution_policy(
        _profile(),
        _plan(),
        _approval_policy(),
        entity_available=True,
    )

    assert decision.status == DECISION_APPROVAL_REQUIRED
    assert decision.reasons == ()
    assert decision.execution_performed is False


def test_approval_mode_requires_explicit_limits() -> None:
    with pytest.raises(ValueError, match="requires max_power_kw"):
        LoadExecutionPolicy(
            "ev-home",
            mode=EXECUTION_MODE_APPROVAL_REQUIRED,
            max_duration_minutes=120,
        ).validated()
    with pytest.raises(ValueError, match="requires max_duration_minutes"):
        LoadExecutionPolicy(
            "ev-home",
            mode=EXECUTION_MODE_APPROVAL_REQUIRED,
            max_power_kw=11.0,
        ).validated()


def test_missing_or_unavailable_entity_blocks() -> None:
    missing = evaluate_execution_policy(
        _profile(entity_id=None),
        _plan(),
        _approval_policy(),
        entity_available=None,
    )
    unavailable = evaluate_execution_policy(
        _profile(),
        _plan(),
        _approval_policy(),
        entity_available=False,
    )

    assert missing.status == DECISION_BLOCKED
    assert REASON_ENTITY_BINDING_REQUIRED in missing.reasons
    assert unavailable.status == DECISION_BLOCKED
    assert REASON_ENTITY_UNAVAILABLE in unavailable.reasons


def test_disabled_profile_and_mismatched_plan_block() -> None:
    decision = evaluate_execution_policy(
        _profile(enabled=False),
        _plan(load_id="other-profile"),
        _approval_policy(),
        entity_available=True,
    )

    assert decision.status == DECISION_BLOCKED
    assert REASON_PROFILE_DISABLED in decision.reasons
    assert REASON_PLAN_PROFILE_MISMATCH in decision.reasons


def test_power_and_duration_caps_are_fail_closed() -> None:
    decision = evaluate_execution_policy(
        _profile(),
        _plan(power_kw=12.0, duration_minutes=135),
        _approval_policy(),
        entity_available=True,
    )

    assert decision.status == DECISION_BLOCKED
    assert REASON_POWER_LIMIT_EXCEEDED in decision.reasons
    assert REASON_DURATION_LIMIT_EXCEEDED in decision.reasons
    assert decision.execution_performed is False


def test_automatic_mode_is_not_supported() -> None:
    with pytest.raises(ValueError, match="unsupported execution mode"):
        LoadExecutionPolicy(
            "ev-home",
            mode="automatic",
            max_power_kw=11.0,
            max_duration_minutes=120,
        ).validated()
