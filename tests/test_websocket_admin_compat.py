from __future__ import annotations

from pathlib import Path


INTEGRATION_ROOT = Path("custom_components/frakon_energy")


def test_removed_active_connection_require_admin_is_not_used() -> None:
    """Current Home Assistant ActiveConnection has no require_admin method."""
    violations = []
    for path in INTEGRATION_ROOT.rglob("*.py"):
        if path.name == "ws_auth.py":
            continue
        if "connection.require_admin()" in path.read_text(encoding="utf-8"):
            violations.append(str(path))

    assert not violations, (
        "Removed Home Assistant websocket admin API is still used: "
        + ", ".join(violations)
    )


def test_admin_guard_uses_current_home_assistant_authorization_contract() -> None:
    source = (INTEGRATION_ROOT / "ws_auth.py").read_text(encoding="utf-8")
    assert "connection.require_admin()" not in source
    assert "user.is_admin" in source
    assert "Unauthorized" in source
