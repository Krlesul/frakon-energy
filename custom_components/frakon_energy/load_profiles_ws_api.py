"""Home Assistant WebSocket API for persistent FRAKON Energy load profiles."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .load_execution_policy_options import delete_policy
from .load_profiles import (
    PROFILE_KINDS,
    LoadProfile,
    delete_profile,
    profiles_from_options,
    upsert_profile,
)

COMMAND_LIST = f"{DOMAIN}/load_profiles/list"
COMMAND_UPSERT = f"{DOMAIN}/load_profiles/upsert"
COMMAND_DELETE = f"{DOMAIN}/load_profiles/delete"
_REGISTERED_KEY = "load_profiles_websocket_registered"


def _entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ValueError("FRAKON Energy config entry not found")
    return entry


def _payload(entry: ConfigEntry) -> dict[str, Any]:
    profiles = profiles_from_options(entry.options)
    return {
        "entry_id": entry.entry_id,
        "profiles": [profile.as_dict() for profile in profiles],
        "kinds": list(PROFILE_KINDS),
        "read_only_execution": True,
    }


@callback
def async_register_load_profiles_websocket(hass: HomeAssistant) -> None:
    """Register persistent load-profile CRUD commands once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command(
        {vol.Required("type"): COMMAND_LIST, vol.Required("entry_id"): str}
    )
    @websocket_api.async_response
    async def websocket_list(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            entry = _entry(hass, msg["entry_id"])
            result = _payload(entry)
        except (ValueError, TypeError) as err:
            connection.send_error(msg["id"], "invalid_load_profiles", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_UPSERT,
            vol.Required("entry_id"): str,
            vol.Required("profile_id"): str,
            vol.Required("name"): str,
            vol.Required("kind"): vol.In(PROFILE_KINDS),
            vol.Required("duration_minutes"): vol.All(int, vol.Range(min=1)),
            vol.Required("power_kw"): vol.All(vol.Coerce(float), vol.Range(min=0.001)),
            vol.Optional("enabled", default=True): bool,
            vol.Optional("entity_id"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_upsert(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            entry = _entry(hass, msg["entry_id"])
            profile = LoadProfile(
                profile_id=msg["profile_id"],
                name=msg["name"],
                kind=msg["kind"],
                duration_minutes=msg["duration_minutes"],
                power_kw=msg["power_kw"],
                enabled=msg["enabled"],
                entity_id=(msg.get("entity_id") or "").strip() or None,
            ).validated()
            options = upsert_profile(entry.options, profile)
            hass.config_entries.async_update_entry(entry, options=options)
        except (ValueError, TypeError) as err:
            connection.send_error(msg["id"], "invalid_load_profile", str(err))
            return
        connection.send_result(msg["id"], _payload(entry))

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_DELETE,
            vol.Required("entry_id"): str,
            vol.Required("profile_id"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_delete(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            entry = _entry(hass, msg["entry_id"])
            options = delete_profile(entry.options, msg["profile_id"])
            options = delete_policy(options, msg["profile_id"], missing_ok=True)
            hass.config_entries.async_update_entry(entry, options=options)
        except (ValueError, TypeError) as err:
            connection.send_error(msg["id"], "invalid_load_profile", str(err))
            return
        connection.send_result(msg["id"], _payload(entry))

    websocket_api.async_register_command(hass, websocket_list)
    websocket_api.async_register_command(hass, websocket_upsert)
    websocket_api.async_register_command(hass, websocket_delete)
    domain_data[_REGISTERED_KEY] = True
