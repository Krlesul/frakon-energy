"""Read-only Home Assistant WebSocket API for confirmed daily all-in costs."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

import voluptuous as vol
from homeassistant.components import websocket_api
from .ws_auth import ensure_admin
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .daily_all_in_costs import (
    price_confirmed_daily_consumption,
    summarize_daily_all_in_costs,
)

COMMAND_DAILY_ALL_IN_COSTS = "frakon_energy/tariff/daily_costs"
_REGISTERED_KEY = "daily_all_in_costs_websocket_registered"
_MAX_RANGE_DAYS = 366


def _parse_day(value: Any, field: str) -> date:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 date")
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"{field} must be an ISO-8601 date") from err


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


def _daily_history(hass: HomeAssistant, entry_id: str) -> tuple[Any, ...]:
    coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
    history = getattr(coordinator, "history", None)
    daily_consumption = getattr(history, "daily_consumption", None)
    if not callable(daily_consumption):
        raise LookupError("VisionQ daily history is not available for this entry")
    records = daily_consumption()
    if not isinstance(records, tuple):
        records = tuple(records)
    return records


@callback
def async_register_daily_all_in_costs_websocket(hass: HomeAssistant) -> None:
    """Register exact daily customer cost lookup once."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_DAILY_ALL_IN_COSTS,
            vol.Required("entry_id"): str,
            vol.Required("start_day"): str,
            vol.Required("end_day"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_daily_all_in_costs(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        ensure_admin(connection)
        entry = _entry_or_error(hass, connection, msg)
        if entry is None:
            return
        try:
            start_day = _parse_day(msg["start_day"], "start_day")
            end_day = _parse_day(msg["end_day"], "end_day")
            if end_day < start_day:
                raise ValueError("end_day must not precede start_day")
            range_days = (end_day - start_day).days + 1
            if range_days > _MAX_RANGE_DAYS:
                raise ValueError(
                    f"daily cost range must not exceed {_MAX_RANGE_DAYS} days"
                )
            raw_records = _daily_history(hass, entry.entry_id)
            selected = tuple(
                item
                for item in raw_records
                if start_day <= getattr(item, "day", date.min) <= end_day
            )
            priced = price_confirmed_daily_consumption(entry.options, selected)
        except ValueError as err:
            connection.send_error(
                msg["id"],
                "invalid_daily_cost_request",
                str(err),
            )
            return
        except LookupError as err:
            connection.send_error(
                msg["id"],
                "daily_cost_tariff_unavailable",
                str(err),
            )
            return
        except Exception as err:
            connection.send_error(
                msg["id"],
                "daily_cost_unavailable",
                str(err),
            )
            return

        connection.send_result(
            msg["id"],
            {
                "entry_id": entry.entry_id,
                "start_day": start_day.isoformat(),
                "end_day": end_day.isoformat(),
                "price_source": "confirmed_all_in",
                "fixed_monthly_excluded": True,
                "records": [item.as_dict() for item in priced],
                "summary": summarize_daily_all_in_costs(priced),
                "read_only": True,
                "persistence_performed": False,
                "activation_performed": False,
            },
        )

    websocket_api.async_register_command(hass, websocket_daily_all_in_costs)
    domain_data[_REGISTERED_KEY] = True
