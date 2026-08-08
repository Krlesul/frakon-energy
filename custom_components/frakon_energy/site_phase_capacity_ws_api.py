"""Administrator-only Site Phase Capacity diagnostics API."""

from __future__ import annotations

import math
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .site_phase_capacity import (
    CONF_MAX_PHASE_CURRENT_A,
    build_site_phase_capacity_status,
    update_site_phase_capacity_limit,
)

COMMAND_SITE_PHASE_CAPACITY_STATUS = f"{DOMAIN}/site_phase_capacity/status"
COMMAND_SITE_PHASE_CAPACITY_SET = f"{DOMAIN}/site_phase_capacity/set"
_REGISTERED_KEY = "site_phase_capacity_websocket_registered"


def _entry(hass: HomeAssistant, entry_id: str):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ValueError("FRAKON Energy config entry not found")
    return entry


def _finite_positive_amps(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as err:
        raise vol.Invalid("max_phase_current_a must be numeric") from err
    if not math.isfinite(number) or number <= 0:
        raise vol.Invalid("max_phase_current_a must be a finite positive number")
    return number


async def async_site_phase_capacity_status(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> dict[str, Any]:
    entry = _entry(hass, entry_id)
    return build_site_phase_capacity_status(
        hass,
        entry_id=entry_id,
        options=entry.options,
    ).as_dict()


@callback
def async_register_site_phase_capacity_websocket(hass: HomeAssistant) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_SITE_PHASE_CAPACITY_STATUS,
            vol.Required("entry_id"): str,
        }
    )
    async def websocket_status(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_site_phase_capacity_status(hass, entry_id=msg["entry_id"])
        except ValueError as err:
            connection.send_error(msg["id"], "site_phase_capacity_status_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "site_phase_capacity_status_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_SITE_PHASE_CAPACITY_SET,
            vol.Required("entry_id"): str,
            vol.Required(CONF_MAX_PHASE_CURRENT_A): vol.Any(None, _finite_positive_amps),
        }
    )
    async def websocket_set(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            entry = _entry(hass, msg["entry_id"])
            value = msg[CONF_MAX_PHASE_CURRENT_A]
            limit = None if value is None else float(value)
            options = update_site_phase_capacity_limit(entry.options, limit)
            hass.config_entries.async_update_entry(entry, options=options)
            result = build_site_phase_capacity_status(
                hass,
                entry_id=entry.entry_id,
                options=options,
            ).as_dict()
        except ValueError as err:
            connection.send_error(msg["id"], "site_phase_capacity_set_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "site_phase_capacity_set_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_status)
    websocket_api.async_register_command(hass, websocket_set)
    domain_data[_REGISTERED_KEY] = True
