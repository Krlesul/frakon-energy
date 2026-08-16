from __future__ import annotations

from typing import Any, Mapping

import voluptuous as vol
from homeassistant.components import websocket_api
from .ws_auth import ensure_admin
from homeassistant.core import HomeAssistant, callback

from .entity_discovery_lifecycle import EntityDiscoveryRuntimeRegistry
from .entity_discovery_websocket import (
    COMMAND_GET,
    COMMAND_REMOVE,
    COMMAND_RESCAN,
    COMMAND_SAVE,
)

_REGISTERED_KEY = "entity_discovery_websocket_registered"


def _command_schema(command: str, *, include_entity: bool = False) -> dict[vol.Marker, Any]:
    schema: dict[vol.Marker, Any] = {
        vol.Required("type"): command,
        vol.Required("entry_id"): str,
        vol.Optional("include_unavailable", default=False): bool,
    }
    if command in (COMMAND_SAVE, COMMAND_REMOVE):
        schema[vol.Required("technology")] = str
        schema[vol.Required("role")] = str
    if include_entity:
        schema[vol.Required("entity_id")] = str
    return schema


def _runtime(
    runtime_registry: EntityDiscoveryRuntimeRegistry,
    msg: Mapping[str, Any],
):
    return runtime_registry.get(str(msg["entry_id"]))


def _send_runtime_error(
    connection: websocket_api.ActiveConnection,
    msg: Mapping[str, Any],
    err: Exception,
) -> None:
    connection.send_error(
        msg["id"],
        "entity_discovery_runtime_unavailable",
        str(err),
    )


@callback
def async_register_entity_discovery_websocket(
    hass: HomeAssistant,
    runtime_registry: EntityDiscoveryRuntimeRegistry,
) -> None:
    """Register FRAKON Energy entity-discovery WebSocket commands once.

    Commands resolve the runtime from the requested config-entry id so installations
    with multiple FRAKON Energy entries never read or modify another entry's mapping.
    """

    domain_data = hass.data.setdefault("frakon_energy", {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command(_command_schema(COMMAND_GET))
    @websocket_api.async_response
    async def websocket_get(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        try:
            result = _runtime(runtime_registry, msg).dispatch(
                COMMAND_GET, msg, is_admin=connection.user.is_admin
            )
        except (KeyError, ValueError) as err:
            _send_runtime_error(connection, msg, err)
            return
        connection.send_result(msg["id"], result)

    @websocket_api.websocket_command(_command_schema(COMMAND_RESCAN))
    @websocket_api.async_response
    async def websocket_rescan(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        ensure_admin(connection)
        try:
            result = _runtime(runtime_registry, msg).dispatch(
                COMMAND_RESCAN, msg, is_admin=True
            )
        except (KeyError, ValueError) as err:
            _send_runtime_error(connection, msg, err)
            return
        connection.send_result(msg["id"], result)

    @websocket_api.websocket_command(_command_schema(COMMAND_SAVE, include_entity=True))
    @websocket_api.async_response
    async def websocket_save(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        ensure_admin(connection)
        try:
            result = _runtime(runtime_registry, msg).dispatch(
                COMMAND_SAVE, msg, is_admin=True
            )
        except (KeyError, ValueError) as err:
            _send_runtime_error(connection, msg, err)
            return
        connection.send_result(msg["id"], result)

    @websocket_api.websocket_command(_command_schema(COMMAND_REMOVE))
    @websocket_api.async_response
    async def websocket_remove(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        ensure_admin(connection)
        try:
            result = _runtime(runtime_registry, msg).dispatch(
                COMMAND_REMOVE, msg, is_admin=True
            )
        except (KeyError, ValueError) as err:
            _send_runtime_error(connection, msg, err)
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_get)
    websocket_api.async_register_command(hass, websocket_rescan)
    websocket_api.async_register_command(hass, websocket_save)
    websocket_api.async_register_command(hass, websocket_remove)
    domain_data[_REGISTERED_KEY] = True
