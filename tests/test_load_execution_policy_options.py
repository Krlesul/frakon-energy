import pytest

from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    EXECUTION_MODE_DISABLED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_policy_options import (
    OPTION_LOAD_EXECUTION_POLICIES,
    delete_policy,
    policies_from_options,
    policy_by_profile_id,
    upsert_policy,
)


def test_missing_policy_defaults_fail_closed() -> None:
    policy = policy_by_profile_id({}, "ev")
    assert policy.profile_id == "ev"
    assert policy.mode == EXECUTION_MODE_DISABLED
    assert policy.max_power_kw is None
    assert policy.max_duration_minutes is None


def test_policy_round_trip_preserves_unrelated_options() -> None:
    original = {"vat_percent": 21.0, "other": "keep"}
    policy = LoadExecutionPolicy(
        profile_id="ev",
        mode=EXECUTION_MODE_APPROVAL_REQUIRED,
        max_power_kw=11.0,
        max_duration_minutes=180,
    )
    updated = upsert_policy(original, policy)
    assert updated["vat_percent"] == 21.0
    assert updated["other"] == "keep"
    assert policies_from_options(updated) == (policy,)
    assert policy_by_profile_id(updated, "ev") == policy


def test_upsert_replaces_without_duplicate() -> None:
    first = LoadExecutionPolicy("ev", EXECUTION_MODE_DISABLED)
    replacement = LoadExecutionPolicy("ev", EXECUTION_MODE_APPROVAL_REQUIRED, 7.2, 120)
    options = upsert_policy({}, first)
    options = upsert_policy(options, replacement)
    assert policies_from_options(options) == (replacement,)
    assert len(options[OPTION_LOAD_EXECUTION_POLICIES]) == 1


def test_duplicate_persisted_policy_is_rejected() -> None:
    value = LoadExecutionPolicy("ev", EXECUTION_MODE_DISABLED).as_dict()
    with pytest.raises(ValueError, match="duplicate execution policy"):
        policies_from_options({OPTION_LOAD_EXECUTION_POLICIES: [value, value]})


def test_delete_policy_can_be_idempotent() -> None:
    original = {"other": 1}
    updated = delete_policy(original, "missing", missing_ok=True)
    assert updated["other"] == 1
    assert updated[OPTION_LOAD_EXECUTION_POLICIES] == []


def test_delete_policy_removes_only_target() -> None:
    ev = LoadExecutionPolicy("ev", EXECUTION_MODE_DISABLED)
    boiler = LoadExecutionPolicy("boiler", EXECUTION_MODE_DISABLED)
    options = upsert_policy({}, ev)
    options = upsert_policy(options, boiler)
    updated = delete_policy(options, "ev")
    assert policies_from_options(updated) == (boiler,)
