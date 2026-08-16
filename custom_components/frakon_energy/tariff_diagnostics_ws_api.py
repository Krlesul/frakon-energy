"""Read-only Home Assistant WebSocket API for confirmed tariff diagnostics."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .tariff_diagnostics import build_tariff_diagnostics

COMMAND_TARIFF_DIAGNOSTICS = "frakon_energy/tariff/diagnostics"
_REGISTERED_KEY = "tariff_diagnostics_websocket_registered"


def _parse_day(value: Any) -> date:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("day must be an ISO-8601 date")
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise ValueError("day must be an ISO-8601 date") from err


def _entry_or_error(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: Mapping[str, Any],
):
    entry = hass.config_entries.async_get_entry(str(msg["entry_id"]))
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(
            msg["id"],
            "entry_not_found",
            "FRAKON Energy config entry was not found.",
        )
        return None
    return entry


@callback
def async_register_tariff_diagnostics_websocket(hass: HomeAssistant) -> None:
    """Register exact confirmed tariff diagnostics once."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_TARIFF_DIAGNOSTICS,
            vol.Required("entry_id"): str,
            vol.Required("day"): str,
        }
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    async def websocket_tariff_diagnostics(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        entry = _entry_or_error(hass, connection, msg)
        if entry is None:
            return
        try:
            diagnostic_day = _parse_day(msg["day"])
            snapshot = build_tariff_diagnostics(
                entry.options,
                day=diagnostic_day,
            )
        except ValueError as err:
            connection.send_error(
                msg["id"],
                "invalid_tariff_diagnostics_request",
                str(err),
            )
            return
        except LookupError as err:
            connection.send_error(
                msg["id"],
                "tariff_diagnostics_unavailable",
                str(err),
            )
            return
        except Exception as err:
            connection.send_error(
                msg["id"],
                "tariff_diagnostics_failed",
                str(err),
            )
            return

        payload = snapshot.as_dict()
        payload["entry_id"] = entry.entry_id
        connection.send_result(msg["id"], payload)

    websocket_api.async_register_command(hass, websocket_tariff_diagnostics)
    domain_data[_REGISTERED_KEY] = True
