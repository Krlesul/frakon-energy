"""Home Assistant WebSocket API for FRAKON Energy load-plan previews."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .energy_load_planner import FlexibleLoad, plan_flexible_load
from .spot_price_ws_api import async_customer_spot_payload

COMMAND_PREVIEW_LOAD_PLAN = f"{DOMAIN}/load_plan/preview"
_REGISTERED_KEY = "load_plan_websocket_registered"


def _parse_datetime(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"{field} must be an ISO-8601 datetime") from err
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return parsed


def _available_intervals(payload: dict[str, Any]) -> list[dict[str, Any]]:
    intervals: list[dict[str, Any]] = []
    for day_name in ("today", "tomorrow"):
        day = payload.get(day_name)
        if not isinstance(day, dict):
            continue
        day_intervals = day.get("intervals")
        if isinstance(day_intervals, list):
            intervals.extend(item for item in day_intervals if isinstance(item, dict))
    return intervals


async def async_preview_load_plan(
    hass: HomeAssistant,
    *,
    load_id: str,
    name: str,
    duration_minutes: int,
    power_kw: float,
    earliest_start: datetime | None = None,
    deadline: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Calculate a read-only load-plan preview from current customer spot prices."""
    current = now or datetime.now(timezone.utc)
    effective_earliest = earliest_start or current
    payload = await async_customer_spot_payload(hass, now=current)
    plan = plan_flexible_load(
        _available_intervals(payload),
        FlexibleLoad(
            load_id=load_id,
            name=name,
            duration_minutes=duration_minutes,
            power_kw=power_kw,
            earliest_start=effective_earliest,
            deadline=deadline,
        ),
    )
    if plan is None:
        return None
    result = plan.as_dict()
    result["read_only"] = True
    result["price_source"] = payload.get("provider")
    result["exchange_rate"] = payload.get("exchange_rate")
    result["spot_data_stale"] = payload.get("stale", False)
    result["spot_fallback_used"] = payload.get("fallback_used", False)
    return result


@callback
def async_register_load_plan_websocket(hass: HomeAssistant) -> None:
    """Register the read-only flexible-load planning command once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_PREVIEW_LOAD_PLAN,
            vol.Required("load_id"): str,
            vol.Required("name"): str,
            vol.Required("duration_minutes"): vol.All(int, vol.Range(min=1)),
            vol.Required("power_kw"): vol.All(vol.Coerce(float), vol.Range(min=0.001)),
            vol.Optional("earliest_start"): str,
            vol.Optional("deadline"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_preview_load_plan(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            earliest_start = _parse_datetime(msg.get("earliest_start"), "earliest_start")
            deadline = _parse_datetime(msg.get("deadline"), "deadline")
            plan = await async_preview_load_plan(
                hass,
                load_id=msg["load_id"],
                name=msg["name"],
                duration_minutes=msg["duration_minutes"],
                power_kw=msg["power_kw"],
                earliest_start=earliest_start,
                deadline=deadline,
            )
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_load_plan", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "load_plan_unavailable", str(err))
            return

        connection.send_result(
            msg["id"],
            {
                "available": plan is not None,
                "plan": plan,
                "command": COMMAND_PREVIEW_LOAD_PLAN,
                "read_only": True,
            },
        )

    websocket_api.async_register_command(hass, websocket_preview_load_plan)
    domain_data[_REGISTERED_KEY] = True
