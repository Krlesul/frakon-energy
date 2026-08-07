import pytest

from custom_components.frakon_energy.load_action_intent import (
    ACTION_STATE_ALREADY_SATISFIED,
    ACTION_STATE_BLOCKED,
    ACTION_STATE_READY,
    UnsupportedActionIntentError,
    evaluate_action_current_state,
    resolve_start_action_intent,
)
from custom_components.frakon_energy.load_profiles import (
    PROFILE_KIND_BATTERY,
    PROFILE_KIND_BOILER,
    PROFILE_KIND_EV,
    PROFILE_KIND_GENERIC,
    LoadProfile,
)


def _profile(
    *,
    kind: str = PROFILE_KIND_EV,
    entity_id: str | None = "switch.enyaq_charging",
    enabled: bool = True,
) -> LoadProfile:
    return LoadProfile(
        "load-1",
        "Load",
        kind,
        60,
        2.0,
        enabled=enabled,
        entity_id=entity_id,
    )


def test_ev_switch_maps_only_to_fixed_switch_turn_on() -> None:
    intent = resolve_start_action_intent(_profile())

    assert intent.action == "start_load"
    assert intent.entity_domain == "switch"
    assert intent.service_domain == "switch"
    assert intent.service_name == "turn_on"
    assert intent.target == {"entity_id": "switch.enyaq_charging"}
    assert intent.service_data == {}
    assert intent.desired_state == "on"
    assert intent.executor_available is False
    assert intent.service_call_performed is False


def test_boiler_switch_uses_same_fixed_mapping() -> None:
    intent = resolve_start_action_intent(
        _profile(kind=PROFILE_KIND_BOILER, entity_id="switch.boiler")
    )

    assert intent.service_domain == "switch"
    assert intent.service_name == "turn_on"
    assert intent.target == {"entity_id": "switch.boiler"}


def test_generic_input_boolean_maps_to_input_boolean_turn_on() -> None:
    intent = resolve_start_action_intent(
        _profile(kind=PROFILE_KIND_GENERIC, entity_id="input_boolean.flexible_load")
    )

    assert intent.entity_domain == "input_boolean"
    assert intent.service_domain == "input_boolean"
    assert intent.service_name == "turn_on"
    assert intent.service_data == {}


@pytest.mark.parametrize(
    "entity_id",
    [
        "button.start_charge",
        "climate.garage",
        "water_heater.boiler",
        "number.charge_current",
        "select.charge_mode",
    ],
)
def test_ambiguous_or_non_idempotent_domains_fail_closed(entity_id: str) -> None:
    with pytest.raises(UnsupportedActionIntentError, match="unsupported entity domain"):
        resolve_start_action_intent(_profile(entity_id=entity_id))


def test_battery_action_is_not_defined() -> None:
    with pytest.raises(UnsupportedActionIntentError, match="battery start action"):
        resolve_start_action_intent(
            _profile(kind=PROFILE_KIND_BATTERY, entity_id="switch.battery")
        )


def test_ev_cannot_use_input_boolean_mapping() -> None:
    with pytest.raises(UnsupportedActionIntentError, match="not allowed"):
        resolve_start_action_intent(
            _profile(kind=PROFILE_KIND_EV, entity_id="input_boolean.ev_charge")
        )


def test_missing_binding_and_disabled_profile_fail_closed() -> None:
    with pytest.raises(UnsupportedActionIntentError, match="entity binding"):
        resolve_start_action_intent(_profile(entity_id=None))
    with pytest.raises(UnsupportedActionIntentError, match="disabled profile"):
        resolve_start_action_intent(_profile(enabled=False))


def test_intent_identity_is_deterministic_and_changes_with_binding() -> None:
    first = resolve_start_action_intent(_profile(entity_id="switch.load_a"))
    again = resolve_start_action_intent(_profile(entity_id="switch.load_a"))
    other = resolve_start_action_intent(_profile(entity_id="switch.load_b"))

    assert first.intent_id == again.intent_id
    assert first.intent_id != other.intent_id


def test_off_state_is_ready_without_service_call() -> None:
    decision = evaluate_action_current_state(resolve_start_action_intent(_profile()), "off")

    assert decision.status == ACTION_STATE_READY
    assert decision.reason == "entity_state_allows_start"
    assert decision.service_call_performed is False
    assert decision.executor_available is False


def test_on_state_is_idempotently_already_satisfied() -> None:
    decision = evaluate_action_current_state(resolve_start_action_intent(_profile()), "on")

    assert decision.status == ACTION_STATE_ALREADY_SATISFIED
    assert decision.reason == "entity_already_in_desired_state"
    assert decision.service_call_performed is False


@pytest.mark.parametrize("state", [None, "unknown", "unavailable", "standby", "idle"])
def test_non_allowlisted_current_states_are_blocked(state: str | None) -> None:
    decision = evaluate_action_current_state(resolve_start_action_intent(_profile()), state)

    assert decision.status == ACTION_STATE_BLOCKED
    assert decision.service_call_performed is False
    assert decision.executor_available is False


def test_action_intent_validation_rejects_arbitrary_service_data() -> None:
    intent = resolve_start_action_intent(_profile())
    object.__setattr__(intent, "service_data", {"malicious": True})

    with pytest.raises(ValueError, match="arbitrary service data"):
        intent.validated()
