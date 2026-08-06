from custom_components.frakon_energy.technology_profile import HouseTechnology
from custom_components.frakon_energy.technology_profile_storage import (
    update_technology_enabled,
)


def test_enables_technology_and_preserves_other_options() -> None:
    options = {
        "language": "cs",
        "technologies": [
            {
                "id": "electric_vehicle",
                "enabled": False,
                "entity_ids": ["sensor.enyaq_battery"],
            }
        ],
    }

    updated = update_technology_enabled(
        options,
        HouseTechnology.ELECTRIC_VEHICLE,
        True,
    )

    assert updated["language"] == "cs"
    vehicle = next(
        item for item in updated["technologies"] if item["id"] == "electric_vehicle"
    )
    assert vehicle["enabled"] is True
    assert vehicle["entity_ids"] == ["sensor.enyaq_battery"]


def test_disables_only_selected_technology() -> None:
    options = {
        "technologies": [
            {"id": "photovoltaics", "enabled": True, "entity_ids": []},
            {"id": "home_battery", "enabled": True, "entity_ids": []},
        ]
    }

    updated = update_technology_enabled(options, "home_battery", False)
    states = {item["id"]: item["enabled"] for item in updated["technologies"]}

    assert states["photovoltaics"] is True
    assert states["home_battery"] is False
