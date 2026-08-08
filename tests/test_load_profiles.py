import pytest

from custom_components.frakon_energy.load_profiles import (
    OPTION_LOAD_PROFILES,
    PHASE_TOPOLOGY_SINGLE,
    PHASE_TOPOLOGY_THREE,
    PHASE_TOPOLOGY_UNKNOWN,
    PROFILE_KIND_BOILER,
    PROFILE_KIND_EV,
    LoadProfile,
    delete_profile,
    profile_by_id,
    profiles_from_options,
    upsert_profile,
)


def test_upsert_preserves_unrelated_options_and_round_trips_profile() -> None:
    original = {"spot_fx_mode": "auto", "other_setting": 7}
    profile = LoadProfile("ev-home", "Enyaq", PROFILE_KIND_EV, 120, 11.0)

    updated = upsert_profile(original, profile)

    assert updated["spot_fx_mode"] == "auto"
    assert updated["other_setting"] == 7
    assert updated[OPTION_LOAD_PROFILES] == [profile.as_dict()]
    assert profiles_from_options(updated) == (profile,)
    assert profile_by_id(updated, "ev-home") == profile


def test_upsert_replaces_existing_profile_without_duplicate() -> None:
    first = LoadProfile("boiler", "Bojler", PROFILE_KIND_BOILER, 60, 2.0)
    replacement = LoadProfile("boiler", "Bojler TUV", PROFILE_KIND_BOILER, 90, 2.5)

    options = upsert_profile({}, first)
    options = upsert_profile(options, replacement)

    profiles = profiles_from_options(options)
    assert profiles == (replacement,)
    assert len(options[OPTION_LOAD_PROFILES]) == 1


def test_delete_profile_keeps_other_profiles_and_options() -> None:
    ev = LoadProfile("ev", "EV", PROFILE_KIND_EV, 120, 11.0)
    boiler = LoadProfile("boiler", "Bojler", PROFILE_KIND_BOILER, 60, 2.0)
    options = upsert_profile({"vat_percent": 21.0}, ev)
    options = upsert_profile(options, boiler)

    updated = delete_profile(options, "ev")

    assert updated["vat_percent"] == 21.0
    assert profiles_from_options(updated) == (boiler,)


def test_profiles_reject_duplicate_ids() -> None:
    profile = LoadProfile("ev", "EV", PROFILE_KIND_EV, 60, 11.0).as_dict()
    with pytest.raises(ValueError, match="duplicate profile_id"):
        profiles_from_options({OPTION_LOAD_PROFILES: [profile, profile]})


def test_profile_validation_rejects_bad_kind_and_duration() -> None:
    with pytest.raises(ValueError, match="unsupported profile kind"):
        LoadProfile("x", "X", "unknown", 60, 1.0).validated()
    with pytest.raises(ValueError, match="multiple of 15"):
        LoadProfile("x", "X", PROFILE_KIND_EV, 20, 1.0).validated()


def test_delete_missing_profile_fails_closed() -> None:
    with pytest.raises(ValueError, match="not found"):
        delete_profile({}, "missing")


def test_entity_binding_round_trips_as_metadata() -> None:
    profile = LoadProfile(
        "ev-home",
        "Enyaq",
        PROFILE_KIND_EV,
        120,
        11.0,
        entity_id="switch.enyaq_charging",
    )

    options = upsert_profile({}, profile)

    assert profile_by_id(options, "ev-home") == profile
    assert options[OPTION_LOAD_PROFILES][0]["entity_id"] == "switch.enyaq_charging"


def test_old_profile_without_entity_binding_or_phase_metadata_remains_compatible() -> None:
    options = {
        OPTION_LOAD_PROFILES: [
            {
                "profile_id": "boiler",
                "name": "Bojler",
                "kind": PROFILE_KIND_BOILER,
                "duration_minutes": 60,
                "power_kw": 2.0,
                "enabled": True,
            }
        ]
    }

    profile = profiles_from_options(options)[0]

    assert profile.entity_id is None
    assert profile.profile_id == "boiler"
    assert profile.phase_topology == PHASE_TOPOLOGY_UNKNOWN
    assert profile.phase_model_ready is False
    assert profile.phase_currents_a() == {"L1": None, "L2": None, "L3": None}


def test_empty_entity_binding_normalizes_to_none() -> None:
    options = {
        OPTION_LOAD_PROFILES: [
            {
                "profile_id": "ev",
                "name": "EV",
                "kind": PROFILE_KIND_EV,
                "duration_minutes": 60,
                "power_kw": 11.0,
                "entity_id": "   ",
            }
        ]
    }

    assert profiles_from_options(options)[0].entity_id is None


def test_profile_validation_rejects_invalid_entity_id() -> None:
    with pytest.raises(ValueError, match="valid Home Assistant entity ID"):
        LoadProfile(
            "ev",
            "EV",
            PROFILE_KIND_EV,
            60,
            11.0,
            entity_id="Not a valid entity",
        ).validated()


def test_single_phase_profile_requires_exactly_one_explicit_phase_current() -> None:
    profile = LoadProfile(
        "boiler-l2",
        "Bojler L2",
        PROFILE_KIND_BOILER,
        60,
        2.0,
        phase_topology=PHASE_TOPOLOGY_SINGLE,
        phase_current_l2_a=8.7,
    ).validated()

    assert profile.phase_model_ready is True
    assert profile.phase_currents_a() == {"L1": None, "L2": 8.7, "L3": None}
    assert profiles_from_options(upsert_profile({}, profile))[0] == profile

    with pytest.raises(ValueError, match="exactly one phase current"):
        LoadProfile(
            "bad-single",
            "Bad",
            PROFILE_KIND_BOILER,
            60,
            2.0,
            phase_topology=PHASE_TOPOLOGY_SINGLE,
            phase_current_l1_a=8.0,
            phase_current_l2_a=8.0,
        ).validated()


def test_three_phase_profile_requires_all_three_explicit_currents() -> None:
    profile = LoadProfile(
        "ev-3p",
        "EV 3f",
        PROFILE_KIND_EV,
        120,
        11.0,
        phase_topology=PHASE_TOPOLOGY_THREE,
        phase_current_l1_a=16.0,
        phase_current_l2_a=16.0,
        phase_current_l3_a=16.0,
    ).validated()

    assert profile.phase_model_ready is True
    assert profile.phase_currents_a() == {"L1": 16.0, "L2": 16.0, "L3": 16.0}

    with pytest.raises(ValueError, match="requires L1, L2 and L3"):
        LoadProfile(
            "bad-3p",
            "Bad 3f",
            PROFILE_KIND_EV,
            120,
            11.0,
            phase_topology=PHASE_TOPOLOGY_THREE,
            phase_current_l1_a=16.0,
            phase_current_l2_a=16.0,
        ).validated()


def test_unknown_topology_rejects_phase_currents_instead_of_guessing() -> None:
    with pytest.raises(ValueError, match="unknown phase topology cannot contain phase currents"):
        LoadProfile(
            "ambiguous",
            "Ambiguous",
            PROFILE_KIND_EV,
            60,
            7.2,
            phase_current_l1_a=16.0,
        ).validated()


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True])
def test_phase_current_must_be_finite_positive(value) -> None:
    with pytest.raises((ValueError, TypeError), match="phase current"):
        LoadProfile(
            "bad-current",
            "Bad current",
            PROFILE_KIND_EV,
            60,
            7.2,
            phase_topology=PHASE_TOPOLOGY_SINGLE,
            phase_current_l1_a=value,
        ).validated()
