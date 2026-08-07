"""Approval preview, issuance and runtime management API for FRAKON Energy.

Issuance and runtime approval management are administrator-only. This module
still exposes no execute/consume command and performs no Home Assistant action.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hmac
import re
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
    VERIFY_POLICY_NOT_ELIGIBLE,
    ApprovalAuthority,
    ApprovalVerification,
    ExecutionApproval,
    execution_snapshot_digest,
)
from .load_execution_policy import DECISION_APPROVAL_REQUIRED, LoadExecutionPolicy
from .load_execution_policy_ws_api import async_evaluate_profile_execution
from .load_profiles import LoadProfile

COMMAND_PREVIEW_APPROVAL = f"{DOMAIN}/load_execution/approval_preview"
COMMAND_ISSUE_APPROVAL = f"{DOMAIN}/load_execution/approval_issue"
COMMAND_LIST_APPROVALS = f"{DOMAIN}/load_execution/approval_list"
COMMAND_VERIFY_APPROVAL = f"{DOMAIN}/load_execution/approval_verify"
COMMAND_REVOKE_APPROVAL = f"{DOMAIN}/load_execution/approval_revoke"
_REGISTERED_KEY = "load_execution_approval_preview_websocket_registered"
_AUTHORITY_KEY = "load_execution_approval_authorities_by_entry"
_RECORDS_KEY = "load_execution_approval_records_by_entry"
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ApprovalScopeChangedError(ValueError):
    """Raised when the scope shown to the user no longer matches fresh state."""


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


def _validate_expected_digest(value: str) -> str:
    if not _DIGEST_PATTERN.fullmatch(value):
        raise ValueError("expected_snapshot_digest must be a lowercase SHA-256 hex digest")
    return value


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


def _candidate_from_evaluation(
    evaluation: dict[str, Any],
) -> tuple[LoadProfile, LoadPlan, LoadExecutionPolicy] | None:
    plan_value = evaluation.get("plan")
    if (
        evaluation.get("status") != DECISION_APPROVAL_REQUIRED
        or evaluation.get("reasons")
        or not isinstance(plan_value, dict)
    ):
        return None

    profile_value = evaluation.get("profile")
    policy_value = evaluation.get("policy")
    if not isinstance(profile_value, dict) or not isinstance(policy_value, dict):
        raise ValueError("approval candidate is missing profile or policy data")

    profile = LoadProfile.from_dict(profile_value)
    policy = LoadExecutionPolicy.from_dict(policy_value)
    plan = _plan_from_dict(profile, plan_value)
    return profile, plan, policy


def _approval_authority(hass: HomeAssistant, entry_id: str) -> ApprovalAuthority:
    """Return one process-local authority per config entry."""
    if not entry_id:
        raise ValueError("entry_id is required for approval authority scope")
    domain_data = hass.data.setdefault(DOMAIN, {})
    authorities = domain_data.get(_AUTHORITY_KEY)
    if not isinstance(authorities, dict):
        authorities = {}
        domain_data[_AUTHORITY_KEY] = authorities
    authority = authorities.get(entry_id)
    if isinstance(authority, ApprovalAuthority):
        return authority
    authority = ApprovalAuthority.ephemeral()
    authorities[entry_id] = authority
    return authority


@dataclass(slots=True)
class RuntimeApprovalRecord:
    """Server-held metadata for one explicitly issued approval artifact."""

    entry_id: str
    profile_id: str
    approved_by: str
    approval: ExecutionApproval
    plan_starts_at: str
    plan_ends_at: str
    revoked: bool = False

    def as_dict(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        if self.revoked:
            status = "revoked"
        elif int(current.timestamp()) >= self.approval.expires_at:
            status = "expired"
        else:
            status = "approved"
        return {
            "entry_id": self.entry_id,
            "profile_id": self.profile_id,
            "approved_by": self.approved_by,
            "status": status,
            "approval": {
                "approval_id": self.approval.approval_id,
                "intent": self.approval.intent,
                "snapshot_digest": self.approval.snapshot_digest,
                "issued_at": self.approval.issued_at,
                "expires_at": self.approval.expires_at,
            },
            "plan_starts_at": self.plan_starts_at,
            "plan_ends_at": self.plan_ends_at,
            "revoked": self.revoked,
            "runtime_only": True,
            "survives_restart": False,
            "execution_performed": False,
            "executor_available": False,
            "can_execute": False,
        }


def _approval_records(hass: HomeAssistant, entry_id: str) -> dict[str, RuntimeApprovalRecord]:
    domain_data = hass.data.setdefault(DOMAIN, {})
    by_entry = domain_data.get(_RECORDS_KEY)
    if not isinstance(by_entry, dict):
        by_entry = {}
        domain_data[_RECORDS_KEY] = by_entry
    records = by_entry.get(entry_id)
    if not isinstance(records, dict):
        records = {}
        by_entry[entry_id] = records
    return records


def _record(hass: HomeAssistant, entry_id: str, approval_id: str) -> RuntimeApprovalRecord:
    records = _approval_records(hass, entry_id)
    try:
        record = records[approval_id]
    except KeyError as err:
        raise ValueError(f"approval not found: {approval_id}") from err
    if not isinstance(record, RuntimeApprovalRecord):
        raise ValueError("invalid runtime approval record")
    return record


def _list_payload(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    return {
        "entry_id": entry_id,
        "approvals": [record.as_dict() for record in _approval_records(hass, entry_id).values()],
        "runtime_only": True,
        "survives_restart": False,
        "execution_performed": False,
        "executor_available": False,
        "can_execute": False,
    }


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
        "can_execute": False,
    }

    candidate = _candidate_from_evaluation(evaluation)
    if candidate is None:
        return result
    profile, plan, policy = candidate
    result["snapshot_digest"] = execution_snapshot_digest(profile, plan, policy)
    result["eligible"] = True
    return result


async def async_issue_execution_approval(
    hass: HomeAssistant,
    *,
    entry_id: str,
    profile_id: str,
    expected_snapshot_digest: str,
    earliest_start: datetime | None = None,
    deadline: datetime | None = None,
    ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
    now: datetime | None = None,
    approved_by: str = "administrator",
) -> dict[str, Any]:
    """Issue a short-lived artifact only if a fresh scope matches the preview digest."""
    expected_digest = _validate_expected_digest(expected_snapshot_digest)
    if ttl_seconds <= 0 or ttl_seconds > MAX_APPROVAL_TTL_SECONDS:
        raise ValueError(f"ttl_seconds must be between 1 and {MAX_APPROVAL_TTL_SECONDS}")
    if not approved_by.strip():
        raise ValueError("approved_by is required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    evaluation = await async_evaluate_profile_execution(
        hass,
        entry_id=entry_id,
        profile_id=profile_id,
        earliest_start=earliest_start,
        deadline=deadline,
        now=current,
    )
    candidate = _candidate_from_evaluation(evaluation)
    if candidate is None:
        raise ValueError("execution candidate is not eligible for approval")
    profile, plan, policy = candidate

    current_digest = execution_snapshot_digest(profile, plan, policy)
    if not hmac.compare_digest(expected_digest, current_digest):
        raise ApprovalScopeChangedError(
            "approval scope changed since preview; refresh the approval preview before issuing"
        )

    entity_available = evaluation.get("entity_available")
    if entity_available is not None and not isinstance(entity_available, bool):
        raise ValueError("execution evaluation returned an invalid entity availability state")

    authority = _approval_authority(hass, entry_id)
    approval = authority.issue(
        profile,
        plan,
        policy,
        entity_available=entity_available,
        now=current,
        ttl_seconds=ttl_seconds,
    )
    _approval_records(hass, entry_id)[approval.approval_id] = RuntimeApprovalRecord(
        entry_id=entry_id,
        profile_id=profile_id,
        approved_by=approved_by,
        approval=approval,
        plan_starts_at=plan.starts_at,
        plan_ends_at=plan.ends_at,
    )
    return {
        "approval_issued": True,
        "approval": approval.as_dict(),
        "approval_id": approval.approval_id,
        "signature": approval.signature,
        "snapshot_digest": approval.snapshot_digest,
        "expected_snapshot_digest": expected_digest,
        "intent": approval.intent,
        "schema_version": APPROVAL_SCHEMA_VERSION,
        "issued_at": approval.issued_at,
        "expires_at": approval.expires_at,
        "ttl_seconds": approval.expires_at - approval.issued_at,
        "entry_id": entry_id,
        "approved_by": approved_by,
        "profile": profile.as_dict(),
        "policy": policy.as_dict(),
        "plan": plan.as_dict(),
        "entity_id": profile.entity_id,
        "entity_available": entity_available,
        "execution_performed": False,
        "executor_available": False,
        "consumed": False,
        "can_execute": False,
    }


async def _fresh_evaluation_for_record(
    hass: HomeAssistant,
    record: RuntimeApprovalRecord,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    return await async_evaluate_profile_execution(
        hass,
        entry_id=record.entry_id,
        profile_id=record.profile_id,
        earliest_start=_parse_datetime(record.plan_starts_at, "plan_starts_at"),
        deadline=_parse_datetime(record.plan_ends_at, "plan_ends_at"),
        now=now,
    )


async def async_verify_execution_approval(
    hass: HomeAssistant,
    *,
    entry_id: str,
    approval_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify a server-held approval against a freshly recalculated exact scope."""
    current = now or datetime.now(timezone.utc)
    record = _record(hass, entry_id, approval_id)
    evaluation = await _fresh_evaluation_for_record(hass, record, now=current)
    candidate = _candidate_from_evaluation(evaluation)
    if candidate is None:
        verification = ApprovalVerification(
            valid=False,
            reason=VERIFY_POLICY_NOT_ELIGIBLE,
            approval_id=record.approval.approval_id,
            snapshot_digest=record.approval.snapshot_digest,
            consumed=False,
            execution_performed=False,
        )
    else:
        profile, plan, policy = candidate
        verification = _approval_authority(hass, entry_id).verify(
            record.approval,
            profile,
            plan,
            policy,
            entity_available=evaluation.get("entity_available"),
            now=current,
        )
    return {
        "record": record.as_dict(now=current),
        "verification": verification.as_dict(),
        "evaluation": evaluation,
        "execution_performed": False,
        "executor_available": False,
        "can_execute": False,
    }


def async_revoke_execution_approval(
    hass: HomeAssistant,
    *,
    entry_id: str,
    approval_id: str,
) -> dict[str, Any]:
    """Revoke a server-held approval without consuming or executing it."""
    record = _record(hass, entry_id, approval_id)
    verification = _approval_authority(hass, entry_id).revoke(record.approval)
    _approval_records(hass, entry_id)[approval_id] = replace(record, revoked=True)
    return {
        "record": _approval_records(hass, entry_id)[approval_id].as_dict(),
        "verification": verification.as_dict(),
        "execution_performed": False,
        "executor_available": False,
        "can_execute": False,
    }


@callback
def async_register_load_execution_approval_preview_websocket(hass: HomeAssistant) -> None:
    """Register preview plus administrator-only approval management commands once."""
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
                int, vol.Range(min=1, max=MAX_APPROVAL_TTL_SECONDS)
            ),
        }
    )
    @websocket_api.async_response
    async def websocket_preview(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
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

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_ISSUE_APPROVAL,
            vol.Required("entry_id"): str,
            vol.Required("profile_id"): str,
            vol.Required("expected_snapshot_digest"): str,
            vol.Optional("earliest_start"): str,
            vol.Optional("deadline"): str,
            vol.Optional("ttl_seconds", default=DEFAULT_APPROVAL_TTL_SECONDS): vol.All(
                int, vol.Range(min=1, max=MAX_APPROVAL_TTL_SECONDS)
            ),
        }
    )
    async def websocket_issue(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
        user = getattr(connection, "user", None)
        approved_by = str(getattr(user, "id", None) or "administrator")
        try:
            result = await async_issue_execution_approval(
                hass,
                entry_id=msg["entry_id"],
                profile_id=msg["profile_id"],
                expected_snapshot_digest=msg["expected_snapshot_digest"],
                earliest_start=_parse_datetime(msg.get("earliest_start"), "earliest_start"),
                deadline=_parse_datetime(msg.get("deadline"), "deadline"),
                ttl_seconds=msg["ttl_seconds"],
                approved_by=approved_by,
            )
        except ApprovalScopeChangedError as err:
            connection.send_error(msg["id"], "execution_approval_scope_changed", str(err))
            return
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_execution_approval_issue", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_approval_issue_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {vol.Required("type"): COMMAND_LIST_APPROVALS, vol.Required("entry_id"): str}
    )
    async def websocket_list(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
        connection.send_result(msg["id"], _list_payload(hass, msg["entry_id"]))

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {vol.Required("type"): COMMAND_VERIFY_APPROVAL, vol.Required("entry_id"): str, vol.Required("approval_id"): str}
    )
    async def websocket_verify(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
        try:
            result = await async_verify_execution_approval(hass, entry_id=msg["entry_id"], approval_id=msg["approval_id"])
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_execution_approval", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_approval_verify_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {vol.Required("type"): COMMAND_REVOKE_APPROVAL, vol.Required("entry_id"): str, vol.Required("approval_id"): str}
    )
    async def websocket_revoke(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
        try:
            result = async_revoke_execution_approval(hass, entry_id=msg["entry_id"], approval_id=msg["approval_id"])
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_execution_approval", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_preview)
    websocket_api.async_register_command(hass, websocket_issue)
    websocket_api.async_register_command(hass, websocket_list)
    websocket_api.async_register_command(hass, websocket_verify)
    websocket_api.async_register_command(hass, websocket_revoke)
    domain_data[_REGISTERED_KEY] = True
