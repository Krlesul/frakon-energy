"""Read-only approval-scope preview API for FRAKON Energy load execution."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .energy_load_planner import LoadPlan
from .load_execution_approval import (
    APPROVAL_INTENT_EXECUTE_LOAD_PLAN,
    APPROVAL_SCHEMA_VERSION,
    DEFAULT_APPROVAL_TTL_SECONDS,
    MAX_APPROVAL_TTL_SECONDS,
    execution_snapshot_digest,
)
from .load_execution_policy import DECISION_APPROVAL_REQUIRED, LoadExecutionPolicy
from .load_execution_policy_ws_api import async_evaluate_profile_execution
from .load_profiles import LoadProfile

COMMAND_PREVIEW_APPROVAL = f"{DOMAIN}/load_execution/approval_preview"
_REGISTERED_KEY = "load_execution_approval_preview_websocket_registered"


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


def _plan_from_dict(profile: LoadProfile, value: dict[str, Any]) -> LoadPlan:
    return LoadPlan(
        load_id=profile.profile_id,
        name=profile.name,
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


async def async_preview_execution_approval(
    hass: HomeAssistant,
    *,
    entry_id: str,
    profile_id: str,
    earliest_start: datetime | None = None,
    deadline: datetime | None = None,
    ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
) -> dict[str, Any]:
    """Show the exact approval scope without issuing an approval artifact."""
    if ttl_seconds <= 0 or ttl_seconds > MAX_APPROVAL_TTL_SECONDS:
        raise ValueError(f"ttl_seconds must be between 1 and {MAX_APPROVAL_TTL_SECONDS}")

    evaluation = await async_evaluate_profile_execution(
        hass,
        entry_id=entry_id,
        profile_id=profile_id,
        earliest_start=earliest_start,
        deadline=deadline,
    )
    result: dict[str, Any] = {
        "eligible": False,
        "status": evaluation["status"],
        "reasons": list(evaluation.get("reasons", [])),
        "intent": APPROVAL_INTENT_EXECUTE_LOAD_PLAN,
        "schema_version": APPROVAL_SCHEMA_VERSION,
        "snapshot_digest": None,
        "profile": evaluation.get("profile"),
        "policy": evaluation.get("policy"),
        "plan": evaluation.get("plan"),
        "entity_id": evaluation.get("entity_id"),
        "entity_available": evaluation.get("entity_available"),
        "ttl_seconds": ttl_seconds,
        "max_ttl_seconds": MAX_APPROVAL_TTL_SECONDS,
        "approval_issued": False,
        "approval_id": None,
        "signature": None,
        "execution_performed": False,
        "executor_available": False,
        "preview_only": True,
    }

    plan_value = evaluation.get("plan")
    if evaluation.get("status") != DECISION_APPROVAL_REQUIRED or not isinstance(plan_value, dict):
        return result

    profile_value = evaluation.get("profile")
    policy_value = evaluation.get("policy")
    if not isinstance(profile_value, dict) or not isinstance(policy_value, dict):
        raise ValueError("approval preview is missing profile or policy data")

    profile = LoadProfile.from_dict(profile_value)
    policy = LoadExecutionPolicy.from_dict(policy_value)
    plan = _plan_from_dict(profile, plan_value)
    result["snapshot_digest"] = execution_snapshot_digest(profile, plan, policy)
    result["eligible"] = True
    return result


@callback
def async_register_load_execution_approval_preview_websocket(hass: HomeAssistant) -> None:
    """Register the read-only approval preview command once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_PREVIEW_APPROVAL,
            vol.Required("entry_id"): str,
            vol.Required("profile_id"): str,
            vol.Optional("earliest_start"): str,
            vol.Optional("deadline"): str,
            vol.Optional("ttl_seconds", default=DEFAULT_APPROVAL_TTL_SECONDS): vol.All(
                int,
                vol.Range(min=1, max=MAX_APPROVAL_TTL_SECONDS),
            ),
        }
    )
    @websocket_api.async_response
    async def websocket_preview(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_preview_execution_approval(
                hass,
                entry_id=msg["entry_id"],
                profile_id=msg["profile_id"],
                earliest_start=_parse_datetime(msg.get("earliest_start"), "earliest_start"),
                deadline=_parse_datetime(msg.get("deadline"), "deadline"),
                ttl_seconds=msg["ttl_seconds"],
            )
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_execution_approval_preview", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_approval_preview_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_preview)
    domain_data[_REGISTERED_KEY] = True
