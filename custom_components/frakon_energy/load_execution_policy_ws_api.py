"""Read-only Home Assistant WebSocket API for FRAKON Energy execution policies."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .energy_load_planner import LoadPlan
from .load_execution_policy import (
    DECISION_BLOCKED,
    EXECUTION_MODE_DISABLED,
    EXECUTION_MODES,
    REASON_PLAN_UNAVAILABLE,
    REASON_POLICY_DISABLED,
    LoadExecutionPolicy,
    delete_execution_policy,
    effective_policy_from_options,
    evaluate_execution_policy,
    policies_from_options,
    upsert_execution_policy,
)
from .load_plan_ws_api import async_preview_load_plan
from .load_profiles import LoadProfile, profile_by_id

COMMAND_LIST_POLICIES = f"{DOMAIN}/load_execution_policies/list"
COMMAND_UPSERT_POLICY = f"{DOMAIN}/load_execution_policies/upsert"
COMMAND_DELETE_POLICY = f"{DOMAIN}/load_execution_policies/delete"
COMMAND_EVALUATE_PROFILE = f"{DOMAIN}/load_execution/evaluate_profile"
_REGISTERED_KEY = "load_execution_policy_websocket_registered"


def _entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ValueError("FRAKON Energy config entry not found")
    return entry


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


def _payload(entry: ConfigEntry) -> dict[str, Any]:
    return {
        "entry_id": entry.entry_id,
        "policies": [policy.as_dict() for policy in policies_from_options(entry.options)],
        "modes": list(EXECUTION_MODES),
        "default_mode": EXECUTION_MODE_DISABLED,
        "executor_available": False,
    }


def _entity_status(hass: HomeAssistant, entity_id: str | None) -> tuple[bool | None, str | None]:
    if not entity_id:
        return None, None
    state = hass.states.get(entity_id)
    if state is None:
        return False, None
    state_value = str(state.state)
    return state_value not in {"unknown", "unavailable"}, state_value


def _load_plan_from_preview(profile: LoadProfile, preview: dict[str, Any]) -> LoadPlan:
    return LoadPlan(
        load_id=profile.profile_id,
        name=profile.name,
        starts_at=str(preview["starts_at"]),
        ends_at=str(preview["ends_at"]),
        duration_minutes=int(preview["duration_minutes"]),
        interval_count=int(preview["interval_count"]),
        power_kw=float(preview["power_kw"]),
        average_czk_kwh=float(preview["average_czk_kwh"]),
        minimum_czk_kwh=float(preview["minimum_czk_kwh"]),
        maximum_czk_kwh=float(preview["maximum_czk_kwh"]),
        estimated_energy_kwh=float(preview["estimated_energy_kwh"]),
        estimated_cost_czk=float(preview["estimated_cost_czk"]),
    )


async def async_evaluate_profile_execution(
    hass: HomeAssistant,
    *,
    entry_id: str,
    profile_id: str,
    earliest_start: datetime | None = None,
    deadline: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate a fresh profile plan against its effective policy without execution."""
    entry = _entry(hass, entry_id)
    profile = profile_by_id(entry.options, profile_id)
    policy = effective_policy_from_options(entry.options, profile_id)
    current = now or datetime.now(timezone.utc)
    preview = await async_preview_load_plan(
        hass,
        load_id=profile.profile_id,
        name=profile.name,
        duration_minutes=profile.duration_minutes,
        power_kw=profile.power_kw,
        earliest_start=earliest_start,
        deadline=deadline,
        now=current,
    )
    entity_available, entity_state = _entity_status(hass, profile.entity_id)

    if preview is None:
        reasons = [REASON_PLAN_UNAVAILABLE]
        if policy.mode == EXECUTION_MODE_DISABLED:
            reasons.append(REASON_POLICY_DISABLED)
        return {
            "status": DECISION_BLOCKED,
            "profile_id": profile.profile_id,
            "entity_id": profile.entity_id,
            "reasons": reasons,
            "profile": profile.as_dict(),
            "policy": policy.as_dict(),
            "plan": None,
            "entity_available": entity_available,
            "entity_state": entity_state,
            "execution_performed": False,
            "executor_available": False,
        }

    decision = evaluate_execution_policy(
        profile,
        _load_plan_from_preview(profile, preview),
        policy,
        entity_available=entity_available,
    )
    result = decision.as_dict()
    result.update(
        {
            "profile": profile.as_dict(),
            "policy": policy.as_dict(),
            "plan": preview,
            "entity_available": entity_available,
            "entity_state": entity_state,
            "executor_available": False,
        }
    )
    return result


@callback
def async_register_load_execution_policy_websocket(hass: HomeAssistant) -> None:
    """Register policy CRUD and read-only evaluation commands once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command(
        {vol.Required("type"): COMMAND_LIST_POLICIES, vol.Required("entry_id"): str}
    )
    @websocket_api.async_response
    async def websocket_list(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = _payload(_entry(hass, msg["entry_id"]))
        except (ValueError, TypeError) as err:
            connection.send_error(msg["id"], "invalid_load_execution_policies", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_UPSERT_POLICY,
            vol.Required("entry_id"): str,
            vol.Required("profile_id"): str,
            vol.Required("mode"): vol.In(EXECUTION_MODES),
            vol.Optional("max_power_kw"): vol.All(vol.Coerce(float), vol.Range(min=0.001)),
            vol.Optional("max_duration_minutes"): vol.All(int, vol.Range(min=1)),
            vol.Optional("require_entity_binding", default=True): bool,
            vol.Optional("require_entity_available", default=True): bool,
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
            profile_by_id(entry.options, msg["profile_id"])
            policy = LoadExecutionPolicy(
                profile_id=msg["profile_id"],
                mode=msg["mode"],
                max_power_kw=msg.get("max_power_kw"),
                max_duration_minutes=msg.get("max_duration_minutes"),
                require_entity_binding=msg["require_entity_binding"],
                require_entity_available=msg["require_entity_available"],
            ).validated()
            options = upsert_execution_policy(entry.options, policy)
            hass.config_entries.async_update_entry(entry, options=options)
        except (ValueError, TypeError) as err:
            connection.send_error(msg["id"], "invalid_load_execution_policy", str(err))
            return
        connection.send_result(msg["id"], _payload(entry))

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_DELETE_POLICY,
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
            profile_by_id(entry.options, msg["profile_id"])
            has_explicit_policy = any(
                policy.profile_id == msg["profile_id"] for policy in policies_from_options(entry.options)
            )
            options = (
                delete_execution_policy(entry.options, msg["profile_id"])
                if has_explicit_policy
                else dict(entry.options)
            )
            hass.config_entries.async_update_entry(entry, options=options)
        except (ValueError, TypeError) as err:
            connection.send_error(msg["id"], "invalid_load_execution_policy", str(err))
            return
        connection.send_result(msg["id"], _payload(entry))

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_EVALUATE_PROFILE,
            vol.Required("entry_id"): str,
            vol.Required("profile_id"): str,
            vol.Optional("earliest_start"): str,
            vol.Optional("deadline"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_evaluate(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_evaluate_profile_execution(
                hass,
                entry_id=msg["entry_id"],
                profile_id=msg["profile_id"],
                earliest_start=_parse_datetime(msg.get("earliest_start"), "earliest_start"),
                deadline=_parse_datetime(msg.get("deadline"), "deadline"),
            )
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_load_execution_evaluation", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "load_execution_evaluation_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_list)
    websocket_api.async_register_command(hass, websocket_upsert)
    websocket_api.async_register_command(hass, websocket_delete)
    websocket_api.async_register_command(hass, websocket_evaluate)
    domain_data[_REGISTERED_KEY] = True
