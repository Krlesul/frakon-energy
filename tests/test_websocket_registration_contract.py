from __future__ import annotations

from pathlib import Path


WS_API_DIR = Path("custom_components/frakon_energy")


def test_every_websocket_api_with_registered_commands_has_one_time_guard() -> None:
    """Prevent reload/multi-entry code from registering duplicate HA WS commands."""
    modules = sorted(WS_API_DIR.glob("*_ws_api.py"))
    command_modules: list[Path] = []
    violations: list[str] = []

    for path in modules:
        source = path.read_text(encoding="utf-8")
        if "websocket_api.async_register_command" not in source:
            continue
        command_modules.append(path)

        required_fragments = (
            "_REGISTERED_KEY",
            "domain_data.get(_REGISTERED_KEY)",
            "domain_data[_REGISTERED_KEY] = True",
        )
        missing = [fragment for fragment in required_fragments if fragment not in source]
        if missing:
            violations.append(f"{path.name}: missing {', '.join(missing)}")

    assert command_modules, "expected at least one FRAKON Energy WebSocket API module"
    assert not violations, (
        "Every FRAKON Energy WebSocket module that registers Home Assistant commands "
        "must be idempotent across config-entry reloads and multiple entries:\n"
        + "\n".join(violations)
    )


def test_websocket_registration_keys_are_unique_per_module() -> None:
    """Avoid two modules accidentally sharing a registration marker."""
    keys: dict[str, str] = {}
    duplicates: list[str] = []

    for path in sorted(WS_API_DIR.glob("*_ws_api.py")):
        source = path.read_text(encoding="utf-8")
        if "websocket_api.async_register_command" not in source:
            continue

        marker = '_REGISTERED_KEY = "'
        start = source.find(marker)
        if start < 0:
            continue
        start += len(marker)
        end = source.find('"', start)
        if end < 0:
            continue
        key = source[start:end]
        previous = keys.get(key)
        if previous is not None:
            duplicates.append(f"{key}: {previous}, {path.name}")
        else:
            keys[key] = path.name

    assert not duplicates, "duplicate WebSocket registration keys:\n" + "\n".join(duplicates)
