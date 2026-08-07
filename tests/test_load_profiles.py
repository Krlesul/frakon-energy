import pytest

from custom_components.frakon_energy.load_profiles import (
    OPTION_LOAD_PROFILES,
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


def test_old_profile_without_entity_binding_remains_compatible() -> None:
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
