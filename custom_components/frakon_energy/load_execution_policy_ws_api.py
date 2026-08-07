"""WebSocket API for persistent, read-only load execution policy management."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .energy_load_planner import LoadPlan
from .load_execution_policy import EXECUTION_MODES, LoadExecutionPolicy, evaluate_execution_policy
from .load_execution_policy_options import (
    delete_policy,
    policies_from_options,
    policy_by_profile_id,
    upsert_policy,
)
from .load_plan_ws_api import _parse_datetime, async_preview_profile_plan
from .load_profiles import profile_by_id, profiles_from_options

COMMAND_LIST = f"{DOMAIN}/load_execution_policies/list"
COMMAND_UPSERT = f"{DOMAIN}/load_execution_policies/upsert"
COMMAND_DELETE = f"{DOMAIN}/load_execution_policies/delete"
COMMAND_EVALUATE = f"{DOMAIN}/load_execution_policies/evaluate"
_REGISTERED_KEY = "load_execution_policy_websocket_registered"


def _entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ValueError("FRAKON Energy config entry not found")
    return entry


def _payload(entry: ConfigEntry) -> dict[str, Any]:
    profiles = profiles_from_options(entry.options)
    stored = policies_from_options(entry.options)
    return {
        "entry_id": entry.entry_id,
        "policies": [policy_by_profile_id(entry.options, profile.profile_id).as_dict() for profile in profiles],
        "stored_policy_count": len(stored),
        "modes": list(EXECUTION_MODES),
        "automatic_execution_supported": False,
        "execution_performed": False,
    }


def _load_plan(value: dict[str, Any]) -> LoadPlan:
    return LoadPlan(
        load_id=str(value["load_id"]),
        name=str(value["name"]),
        starts_at=str(value["starts_at"]),
        ends_at=str(value["ends_at"]),
        duration_minutes=int(value["duration_minutes"]),
        interval_count=int(value["interval_count"]),
        power_kw=float(value["power_kw"]),
        average_czk_kwh=float(value["average_czk_kwh"]),
        minimum_czk_kwh=float(value["minimum_czk_kwh"]),
        maximum_czk_kwh=float(value["maximum_czk_kwh"]),
        estimated_energy_kwh=float(value["estimated_energy_kwh"]),
        estimated_cost_czk=float(value["estimated_cost_czk"]),
    )


async def async_evaluate_profile_policy(
    hass: HomeAssistant,
    *,
    entry_id: str,
    profile_id: str,
    earliest_start: Any = None,
    deadline: Any = None,
) -> dict[str, Any]:
    """Evaluate the effective policy against a current read-only plan preview."""
    entry = _entry(hass, entry_id)
    profile = profile_by_id(entry.options, profile_id)
    policy = policy_by_profile_id(entry.options, profile_id)
    _profile, plan_payload = await async_preview_profile_plan(
        hass,
        entry_id=entry_id,
        profile_id=profile_id,
        earliest_start=earliest_start,
        deadline=deadline,
    )

    state = hass.states.get(profile.entity_id) if profile.entity_id else None
    entity_state = None if state is None else state.state
    entity_available = state is not None and str(state.state).lower() not in {"unknown", "unavailable"}

    if plan_payload is None:
        return {
            "available": False,
            "profile": profile.as_dict(),
            "policy": policy.as_dict(),
            "plan": None,
            "decision": None,
            "entity_state": entity_state,
            "entity_available": entity_available,
            "automatic_execution_supported": False,
            "execution_performed": False,
            "read_only": True,
        }

    decision = evaluate_execution_policy(
        profile,
        _load_plan(plan_payload),
        policy,
        entity_available=entity_available,
    )
    return {
        "available": True,
        "profile": profile.as_dict(),
        "policy": policy.as_dict(),
        "plan": plan_payload,
        "decision": decision.as_dict(),
        "entity_state": entity_state,
        "entity_available": entity_available,
        "automatic_execution_supported": False,
        "execution_performed": False,
        "read_only": True,
    }


@callback
def async_register_load_execution_policy_websocket(hass: HomeAssistant) -> None:
    """Register persistent execution-policy commands once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command({vol.Required("type"): COMMAND_LIST, vol.Required("entry_id"): str})
    @websocket_api.async_response
    async def websocket_list(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
        try:
            result = _payload(_entry(hass, msg["entry_id"]))
        except (ValueError, TypeError) as err:
            connection.send_error(msg["id"], "invalid_load_execution_policies", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_UPSERT,
            vol.Required("entry_id"): str,
            vol.Required("profile_id"): str,
            vol.Required("mode"): vol.In(EXECUTION_MODES),
            vol.Optional("max_power_kw"): vol.Any(None, vol.All(vol.Coerce(float), vol.Range(min=0.001))),
            vol.Optional("max_duration_minutes"): vol.Any(None, vol.All(int, vol.Range(min=1))),
            vol.Optional("require_entity_binding", default=True): bool,
            vol.Optional("require_entity_available", default=True): bool,
        }
    )
    @websocket_api.async_response
    async def websocket_upsert(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
        try:
            entry = _entry(hass, msg["entry_id"])
            profile_by_id(entry.options, msg["profile_id"])
            policy = LoadExecutionPolicy(
                profile_id=msg["profile_id"],
                mode=msg["mode"],
                max_power_kw=msg.get("max_power_kw"),
                max_duration_minutes=msg.get("max_duration_minutes"),
                require_entity_binding=msg["require_entity_binding"],
                require_entity_available=msg["require_entity_available"],
            ).validated()
            hass.config_entries.async_update_entry(entry, options=upsert_policy(entry.options, policy))
        except (ValueError, TypeError) as err:
            connection.send_error(msg["id"], "invalid_load_execution_policy", str(err))
            return
        connection.send_result(msg["id"], _payload(entry))

    @websocket_api.websocket_command(
        {vol.Required("type"): COMMAND_DELETE, vol.Required("entry_id"): str, vol.Required("profile_id"): str}
    )
    @websocket_api.async_response
    async def websocket_delete(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
        try:
            entry = _entry(hass, msg["entry_id"])
            profile_by_id(entry.options, msg["profile_id"])
            hass.config_entries.async_update_entry(entry, options=delete_policy(entry.options, msg["profile_id"], missing_ok=True))
        except (ValueError, TypeError) as err:
            connection.send_error(msg["id"], "invalid_load_execution_policy", str(err))
            return
        connection.send_result(msg["id"], _payload(entry))

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_EVALUATE,
            vol.Required("entry_id"): str,
            vol.Required("profile_id"): str,
            vol.Optional("earliest_start"): str,
            vol.Optional("deadline"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_evaluate(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
        try:
            earliest_start = _parse_datetime(msg.get("earliest_start"), "earliest_start")
            deadline = _parse_datetime(msg.get("deadline"), "deadline")
            result = await async_evaluate_profile_policy(
                hass,
                entry_id=msg["entry_id"],
                profile_id=msg["profile_id"],
                earliest_start=earliest_start,
                deadline=deadline,
            )
        except (ValueError, TypeError) as err:
            connection.send_error(msg["id"], "invalid_load_execution_policy_evaluation", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "load_execution_policy_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_list)
    websocket_api.async_register_command(hass, websocket_upsert)
    websocket_api.async_register_command(hass, websocket_delete)
    websocket_api.async_register_command(hass, websocket_evaluate)
    domain_data[_REGISTERED_KEY] = True
