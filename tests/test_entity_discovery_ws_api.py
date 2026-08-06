from types import SimpleNamespace
from unittest.mock import Mock, patch

from custom_components.frakon_energy.entity_discovery_ws_api import (
    async_register_entity_discovery_websocket,
)


def test_registers_four_commands_only_once() -> None:
    hass = SimpleNamespace(data={})
    runtime = Mock()

    with patch(
        "custom_components.frakon_energy.entity_discovery_ws_api.websocket_api.async_register_command"
    ) as register:
        async_register_entity_discovery_websocket(hass, runtime)
        async_register_entity_discovery_websocket(hass, runtime)

    assert register.call_count == 4
    assert hass.data["frakon_energy"]["entity_discovery_websocket_registered"] is True


def test_registered_handlers_use_runtime_and_admin_guard() -> None:
    hass = SimpleNamespace(data={})
    runtime = Mock()
    runtime.dispatch.return_value = {"ok": True}
    handlers = []

    with patch(
        "custom_components.frakon_energy.entity_discovery_ws_api.websocket_api.async_register_command",
        side_effect=lambda _hass, handler: handlers.append(handler),
    ):
        async_register_entity_discovery_websocket(hass, runtime)

    assert len(handlers) == 4
    command_types = {handler.schema.schema["type"] for handler in handlers}
    assert command_types == {
        "frakon_energy/entity_discovery/get",
        "frakon_energy/entity_discovery/rescan",
        "frakon_energy/entity_discovery/save",
        "frakon_energy/entity_discovery/remove",
    }
