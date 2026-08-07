"""Signed approval preview and explicit issuance API for FRAKON Energy.

The API can preview, explicitly issue, list, verify and revoke short-lived HMAC
approvals. It intentionally exposes no execute or consume command and performs
no Home Assistant service calls.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hmac
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

_RUNTIME_KEY = "load_execution_approval_runtime"
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


def _candidate_from_evaluation(
    evaluation: dict[str, Any],
) -> tuple[LoadProfile, LoadPlan, LoadExecutionPolicy] | None:
    profile_value = evaluation.get("profile")
    plan_value = evaluation.get("plan")
    policy_value = evaluation.get("policy")
    if not isinstance(profile_value, dict) or not isinstance(plan_value, dict) or not isinstance(policy_value, dict):
        return None
    profile = LoadProfile.from_dict(profile_value)
    policy = LoadExecutionPolicy.from_dict(policy_value)
    plan = _plan_from_dict(profile, plan_value)
    return profile, plan, policy


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

    plan_value = evaluation.get("plan")
    if evaluation.get("status") != DECISION_APPROVAL_REQUIRED or not isinstance(plan_value, dict):
        return result

    candidate = _candidate_from_evaluation(evaluation)
    if candidate is None:
        raise ValueError("approval preview is missing profile, plan or policy data")
    profile, plan, policy = candidate
    result["snapshot_digest"] = execution_snapshot_digest(profile, plan, policy)
    result["eligible"] = True
    return result


@dataclass(slots=True)
class RuntimeApprovalRecord:
    """Server-side runtime metadata for an explicitly issued signed approval."""

    entry_id: str
    profile_id: str
    approved_by: str
    approval: ExecutionApproval
    plan_starts_at: str
    plan_ends_at: str
    revoked: bool = False

    def as_dict(self, *, include_signature: bool = False, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        now_ts = int(current.timestamp())
        if self.revoked:
            status = "revoked"
        elif now_ts >= self.approval.expires_at:
            status = "expired"
        else:
            status = "approved"
        approval = self.approval.as_dict()
        if not include_signature:
            approval.pop("signature", None)
        return {
            "entry_id": self.entry_id,
            "profile_id": self.profile_id,
            "approved_by": self.approved_by,
            "status": status,
            "approval": approval,
            "plan_starts_at": self.plan_starts_at,
            "plan_ends_at": self.plan_ends_at,
            "revoked": self.revoked,
            "runtime_only": True,
            "survives_restart": False,
            "execution_performed": False,
            "executor_available": False,
            "can_execute": False,
        }


class ApprovalRuntime:
    """Ephemeral authority and issued artifact registry; restart clears both."""

    def __init__(self) -> None:
        self.authority = ApprovalAuthority.ephemeral()
        self.records: dict[str, RuntimeApprovalRecord] = {}

    def record(self, record: RuntimeApprovalRecord) -> None:
        self.records[record.approval.approval_id] = record

    def get(self, approval_id: str, *, entry_id: str) -> RuntimeApprovalRecord:
        try:
            record = self.records[approval_id]
        except KeyError as err:
            raise ValueError(f"approval not found: {approval_id}") from err
        if record.entry_id != entry_id:
            raise ValueError("approval does not belong to this FRAKON Energy config entry")
        return record

    def list(self, *, entry_id: str) -> tuple[RuntimeApprovalRecord, ...]:
        return tuple(record for record in self.records.values() if record.entry_id == entry_id)


def _runtime(hass: HomeAssistant) -> ApprovalRuntime:
    domain_data = hass.data.setdefault(DOMAIN, {})
    runtime = domain_data.get(_RUNTIME_KEY)
    if isinstance(runtime, ApprovalRuntime):
        return runtime
    runtime = ApprovalRuntime()
    domain_data[_RUNTIME_KEY] = runtime
    return runtime


def _list_payload(runtime: ApprovalRuntime, entry_id: str) -> dict[str, Any]:
    return {
        "entry_id": entry_id,
        "approvals": [record.as_dict() for record in runtime.list(entry_id=entry_id)],
        "runtime_only": True,
        "survives_restart": False,
        "execution_performed": False,
        "executor_available": False,
        "can_execute": False,
    }


async def async_issue_execution_approval(
    hass: HomeAssistant,
    *,
    entry_id: str,
    profile_id: str,
    expected_snapshot_digest: str,
    approved_by: str,
    earliest_start: datetime | None = None,
    deadline: datetime | None = None,
    ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Explicitly issue a signed artifact only for the exact previewed digest."""
    if not expected_snapshot_digest.strip():
        raise ValueError("expected_snapshot_digest is required")
    if not approved_by.strip():
        raise ValueError("approved_by is required")

    preview = await async_preview_execution_approval(
        hass,
        entry_id=entry_id,
        profile_id=profile_id,
        earliest_start=earliest_start,
        deadline=deadline,
        ttl_seconds=ttl_seconds,
    )
    digest = preview.get("snapshot_digest")
    if not preview.get("eligible") or not isinstance(digest, str):
        raise ValueError("execution candidate is not eligible for approval")
    if not hmac.compare_digest(digest, expected_snapshot_digest):
        raise ValueError("approval snapshot changed since preview")

    evaluation = {
        "profile": preview.get("profile"),
        "plan": preview.get("plan"),
        "policy": preview.get("policy"),
    }
    candidate = _candidate_from_evaluation(evaluation)
    if candidate is None:
        raise ValueError("approval candidate is incomplete")
    profile, plan, policy = candidate
    current = now or datetime.now(timezone.utc)
    runtime = _runtime(hass)
    approval = runtime.authority.issue(
        profile,
        plan,
        policy,
        entity_available=preview.get("entity_available"),
        now=current,
        ttl_seconds=ttl_seconds,
    )
    record = RuntimeApprovalRecord(
        entry_id=entry_id,
        profile_id=profile_id,
        approved_by=approved_by,
        approval=approval,
        plan_starts_at=plan.starts_at,
        plan_ends_at=plan.ends_at,
    )
    runtime.record(record)
    return {
        "issued": True,
        "record": record.as_dict(include_signature=True, now=current),
        "preview": preview,
        "execution_performed": False,
        "executor_available": False,
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
    """Verify a server-held approval against the current exact candidate."""
    runtime = _runtime(hass)
    record = runtime.get(approval_id, entry_id=entry_id)
    current = now or datetime.now(timezone.utc)
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
        verification = runtime.authority.verify(
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
    """Revoke a server-held approval; no execution is performed."""
    runtime = _runtime(hass)
    record = runtime.get(approval_id, entry_id=entry_id)
    verification = runtime.authority.revoke(record.approval)
    runtime.records[approval_id] = replace(record, revoked=True)
    return {
        "record": runtime.records[approval_id].as_dict(),
        "verification": verification.as_dict(),
        "execution_performed": False,
        "executor_available": False,
        "can_execute": False,
    }


@callback
def async_register_load_execution_approval_preview_websocket(hass: HomeAssistant) -> None:
    """Register preview and explicit signed-approval commands once."""
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

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_ISSUE_APPROVAL,
            vol.Required("entry_id"): str,
            vol.Required("profile_id"): str,
            vol.Required("intent"): vol.In((APPROVAL_INTENT_EXECUTE_LOAD_PLAN,)),
            vol.Required("expected_snapshot_digest"): str,
            vol.Optional("earliest_start"): str,
            vol.Optional("deadline"): str,
            vol.Optional("ttl_seconds", default=DEFAULT_APPROVAL_TTL_SECONDS): vol.All(
                int,
                vol.Range(min=1, max=MAX_APPROVAL_TTL_SECONDS),
            ),
        }
    )
    @websocket_api.async_response
    async def websocket_issue(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        user = getattr(connection, "user", None)
        approved_by = str(getattr(user, "id", None) or "authenticated-user")
        try:
            result = await async_issue_execution_approval(
                hass,
                entry_id=msg["entry_id"],
                profile_id=msg["profile_id"],
                expected_snapshot_digest=msg["expected_snapshot_digest"],
                approved_by=approved_by,
                earliest_start=_parse_datetime(msg.get("earliest_start"), "earliest_start"),
                deadline=_parse_datetime(msg.get("deadline"), "deadline"),
                ttl_seconds=msg["ttl_seconds"],
            )
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_execution_approval", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_approval_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.websocket_command(
        {vol.Required("type"): COMMAND_LIST_APPROVALS, vol.Required("entry_id"): str}
    )
    @websocket_api.async_response
    async def websocket_list(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        connection.send_result(msg["id"], _list_payload(_runtime(hass), msg["entry_id"]))

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_VERIFY_APPROVAL,
            vol.Required("entry_id"): str,
            vol.Required("approval_id"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_verify(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_verify_execution_approval(
                hass,
                entry_id=msg["entry_id"],
                approval_id=msg["approval_id"],
            )
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_execution_approval", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_approval_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_REVOKE_APPROVAL,
            vol.Required("entry_id"): str,
            vol.Required("approval_id"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_revoke(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = async_revoke_execution_approval(
                hass,
                entry_id=msg["entry_id"],
                approval_id=msg["approval_id"],
            )
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
