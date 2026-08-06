from types import SimpleNamespace
from unittest.mock import Mock, patch

from custom_components.frakon_energy.entity_discovery_lifecycle import (
    EntityDiscoveryRuntimeRegistry,
)
from custom_components.frakon_energy.entity_discovery_ws_api import (
    async_register_entity_discovery_websocket,
)


def test_registers_four_commands_only_once() -> None:
    hass = SimpleNamespace(data={})
    runtime_registry = EntityDiscoveryRuntimeRegistry()

    with patch(
        "custom_components.frakon_energy.entity_discovery_ws_api.websocket_api.async_register_command"
    ) as register:
        async_register_entity_discovery_websocket(hass, runtime_registry)
        async_register_entity_discovery_websocket(hass, runtime_registry)

    assert register.call_count == 4
    assert hass.data["frakon_energy"]["entity_discovery_websocket_registered"] is True


def test_registered_handlers_require_entry_id() -> None:
    hass = SimpleNamespace(data={})
    runtime_registry = EntityDiscoveryRuntimeRegistry()
    handlers = []

    with patch(
        "custom_components.frakon_energy.entity_discovery_ws_api.websocket_api.async_register_command",
        side_effect=lambda _hass, handler: handlers.append(handler),
    ):
        async_register_entity_discovery_websocket(hass, runtime_registry)

    assert len(handlers) == 4
    command_types = {handler.schema.schema["type"] for handler in handlers}
    assert command_types == {
        "frakon_energy/entity_discovery/get",
        "frakon_energy/entity_discovery/rescan",
        "frakon_energy/entity_discovery/save",
        "frakon_energy/entity_discovery/remove",
    }
    assert all("entry_id" in handler.schema.schema for handler in handlers)


def test_get_handler_routes_to_requested_config_entry() -> None:
    hass = SimpleNamespace(data={})
    runtime_registry = EntityDiscoveryRuntimeRegistry()
    first = Mock()
    second = Mock()
    second.dispatch.return_value = {"entry": "second"}
    runtime_registry.register("entry-1", first)
    runtime_registry.register("entry-2", second)
    handlers = []

    with patch(
        "custom_components.frakon_energy.entity_discovery_ws_api.websocket_api.async_register_command",
        side_effect=lambda _hass, handler: handlers.append(handler),
    ):
        async_register_entity_discovery_websocket(hass, runtime_registry)

    connection = Mock()
    connection.user.is_admin = False
    get_handler = next(
        handler
        for handler in handlers
        if handler.schema.schema["type"] == "frakon_energy/entity_discovery/get"
    )

    import asyncio

    asyncio.run(
        get_handler(
            hass,
            connection,
            {
                "id": 7,
                "type": "frakon_energy/entity_discovery/get",
                "entry_id": "entry-2",
                "include_unavailable": False,
            },
        )
    )

    first.dispatch.assert_not_called()
    second.dispatch.assert_called_once()
    connection.send_result.assert_called_once_with(7, {"entry": "second"})
