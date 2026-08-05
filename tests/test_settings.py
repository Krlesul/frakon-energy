from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_settings():
    path = Path("custom_components/frakon_energy/settings.py")
    spec = importlib.util.spec_from_file_location("frakon_energy_settings", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_all_settings_sections_are_exposed_in_order() -> None:
    settings = load_settings()
    payload = settings.public_settings_payload()
    assert [item["key"] for item in payload] == [
        "connection",
        "metering",
        "billing",
        "contract",
        "hdo",
        "documents",
        "diagnostics",
        "updates",
    ]
    assert payload[0]["title"] == "Připojení"
    assert payload[-1]["title"] == "Aktualizace"


def test_connection_credentials_are_never_returned_to_frontend() -> None:
    settings = load_settings()
    safe = settings.redact_connection_data(
        {
            "username": "user@example.com",
            "password": "secret",
            "token": "token-value",
            "access_token": "access-value",
            "refresh_token": "refresh-value",
            "device_id": "meter-1",
            "connected": True,
        }
    )
    assert safe == {
        "device_id": "meter-1",
        "connected": True,
        "username_configured": True,
        "credentials_configured": True,
    }
