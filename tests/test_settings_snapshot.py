from custom_components.frakon_energy.design import (
    AccentFamily,
    AppearanceMode,
    ThemePreferences,
    DesignStudioPreferences,
    default_design_preferences,
)
from custom_components.frakon_energy.settings_snapshot import (
    build_settings_snapshot,
    settings_completion,
)


def test_settings_snapshot_never_exposes_visionq_secrets() -> None:
    snapshot = build_settings_snapshot(
        connection={
            "provider": "visionq",
            "username": "user@example.com",
            "password": "secret",
            "token": "abc",
            "device": "meter-1",
            "connected": True,
        },
        updates={"version": "1.0.0-rc.2"},
    ).as_dict()

    connection = snapshot["connection"]
    assert connection["provider"] == "visionq"
    assert connection["device"] == "meter-1"
    assert connection["connected"] is True
    assert connection["username_configured"] is True
    assert connection["credentials_configured"] is True
    assert "username" not in connection
    assert "password" not in connection
    assert "token" not in connection


def test_snapshot_copies_section_data() -> None:
    billing = {"configured": True, "monthly_advance_czk": 5000}
    snapshot = build_settings_snapshot(billing=billing)
    billing["monthly_advance_czk"] = 1

    assert snapshot.billing["monthly_advance_czk"] == 5000


def test_snapshot_includes_default_design_studio_payload() -> None:
    snapshot = build_settings_snapshot()

    assert snapshot.design["active_layout"] == "Výchozí"
    assert snapshot.design["theme"]["appearance"] == "system"
    assert snapshot.design["theme"]["accent"] == "gold"
    assert len(snapshot.design["layouts"]) == 1


def test_snapshot_serializes_custom_design_preferences() -> None:
    defaults = default_design_preferences()
    custom = DesignStudioPreferences(
        theme=ThemePreferences(
            appearance=AppearanceMode.DARK,
            accent=AccentFamily.SAPPHIRE,
            corner_radius_px=16,
            shadow_strength=20,
        ),
        active_layout=defaults.active_layout,
        layouts=defaults.layouts,
    )

    snapshot = build_settings_snapshot(design=custom)

    assert snapshot.design["theme"]["appearance"] == "dark"
    assert snapshot.design["theme"]["accent"] == "sapphire"
    assert snapshot.design["theme"]["corner_radius_px"] == 16
    assert snapshot.design["theme"]["shadow_strength"] == 20


def test_settings_completion_reports_missing_sections() -> None:
    snapshot = build_settings_snapshot(
        connection={"username": "u", "password": "p"},
        metering={"configured": True},
        billing={"configured": False},
        contract={"configured": True},
        hdo={"configured": False},
        documents={"count": 2},
        updates={"version": "1.0.0-rc.2"},
    )

    completion = settings_completion(snapshot)
    assert completion == {
        "connection": True,
        "metering": True,
        "billing": False,
        "contract": True,
        "hdo": False,
        "documents": True,
        "design": True,
        "diagnostics": True,
        "updates": True,
    }
