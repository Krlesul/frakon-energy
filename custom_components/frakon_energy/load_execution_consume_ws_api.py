"""Admin-only approval consumption with persistent attempt and action snapshot audit.

The transaction order is deliberately verify -> persist immutable action snapshot
-> persist execution attempt -> consume approval. No Home Assistant service call
or device executor exists in this module.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import math
import re
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .energy_load_planner import LoadPlan
from .load_action_intent import UnsupportedActionIntentError, resolve_start_action_intent
from .load_execution_action_snapshot import (
    ActionSnapshotConflictError,
    ExecutionActionSnapshot,
)
from .load_execution_action_snapshot_runtime import action_snapshot_repository
from .load_execution_approval import ExecutionApproval
from .load_execution_approval_ws_api import _approval_authority
from .load_execution_attempt import (
    AttemptConflictError,
    ExecutionAttempt,
    ExecutionAttemptRepository,
    approval_artifact_fingerprint,
    home_assistant_attempt_repository,
)
from .load_execution_policy import effective_policy_from_options
from .load_profiles import LoadProfile, profile_by_id

COMMAND_CONSUME_APPROVAL = f"{DOMAIN}/load_execution/approval_consume"
COMMAND_LIST_ATTEMPTS = f"{DOMAIN}/load_execution_attempts/list"
_REGISTERED_KEY = "load_execution_consume_websocket_registered"
_REPOSITORIES_KEY = "load_execution_attempt_repositories_by_entry"
_LOCKS_KEY = "load_execution_consume_locks_by_entry"
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class ApprovalConsumeError(ValueError):
    """Raised when an approval cannot be safely consumed."""


def _entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ApprovalConsumeError("FRAKON Energy config entry not found")
    return entry


def _attempt_repository(hass: HomeAssistant, entry_id: str) -> ExecutionAttemptRepository:
    domain_data = hass.data.setdefault(DOMAIN, {})
    repositories = domain_data.get(_REPOSITORIES_KEY)
    if not isinstance(repositories, dict):
        repositories = {}
        domain_data[_REPOSITORIES_KEY] = repositories
    repository = repositories.get(entry_id)
    if isinstance(repository, ExecutionAttemptRepository):
        return repository
    repository = home_assistant_attempt_repository(hass, entry_id)
    repositories[entry_id] = repository
    return repository


def _transaction_lock(hass: HomeAssistant, entry_id: str) -> asyncio.Lock:
    domain_data = hass.data.setdefault(DOMAIN, {})
    locks = domain_data.get(_LOCKS_KEY)
    if not isinstance(locks, dict):
        locks = {}
        domain_data[_LOCKS_KEY] = locks
    lock = locks.get(entry_id)
    if isinstance(lock, asyncio.Lock):
        return lock
    lock = asyncio.Lock()
    locks[entry_id] = lock
    return lock


def _entity_state(hass: HomeAssistant, entity_id: str | None) -> str | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    return str(state.state) if state is not None else None


def _entity_available(hass: HomeAssistant, entity_id: str | None) -> bool | None:
    state = _entity_state(hass, entity_id)
    if state is None:
        return None if not entity_id else False
    return state not in {"unknown", "unavailable"}


def _approval_from_dict(value: Any) -> ExecutionApproval:
    if not isinstance(value, dict):
        raise ApprovalConsumeError("approval must be an object")
    approval_id = value.get("approval_id")
    intent = value.get("intent")
    snapshot_digest = value.get("snapshot_digest")
    signature = value.get("signature")
    issued_at = value.get("issued_at")
    expires_at = value.get("expires_at")
    if not isinstance(approval_id, str) or not approval_id:
        raise ApprovalConsumeError("approval_id is required")
    if not isinstance(intent, str) or not intent:
        raise ApprovalConsumeError("approval intent is required")
    if not isinstance(snapshot_digest, str) or not _HEX_64.fullmatch(snapshot_digest):
        raise ApprovalConsumeError("approval snapshot_digest must be a SHA-256 hex digest")
    if not isinstance(signature, str) or not _HEX_64.fullmatch(signature):
        raise ApprovalConsumeError("approval signature must be a SHA-256 HMAC hex digest")
    if not isinstance(issued_at, int) or isinstance(issued_at, bool):
        raise ApprovalConsumeError("approval issued_at must be an integer timestamp")
    if not isinstance(expires_at, int) or isinstance(expires_at, bool):
        raise ApprovalConsumeError("approval expires_at must be an integer timestamp")
    if issued_at < 0 or expires_at <= issued_at:
        raise ApprovalConsumeError("approval timestamps are invalid")
    return ExecutionApproval(
        approval_id=approval_id,
        intent=intent,
        snapshot_digest=snapshot_digest,
        issued_at=issued_at,
        expires_at=expires_at,
        signature=signature,
    )


def _aware_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ApprovalConsumeError(f"plan {field} must be an ISO-8601 datetime")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as err:
        raise ApprovalConsumeError(f"plan {field} must be an ISO-8601 datetime") from err
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ApprovalConsumeError(f"plan {field} must include a timezone offset")
    return parsed


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ApprovalConsumeError(f"plan {field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as err:
        raise ApprovalConsumeError(f"plan {field} must be numeric") from err
    if not math.isfinite(number):
        raise ApprovalConsumeError(f"plan {field} must be finite")
    return number


def _plan_from_snapshot(profile: LoadProfile, value: Any, *, now: datetime) -> LoadPlan:
    if not isinstance(value, dict):
        raise ApprovalConsumeError("plan must be an object")
    starts = _aware_datetime(value.get("starts_at"), "starts_at")
    ends = _aware_datetime(value.get("ends_at"), "ends_at")
    if starts <= now:
        raise ApprovalConsumeError("approved plan has already started or is stale")
    if ends <= starts:
        raise ApprovalConsumeError("plan ends_at must be after starts_at")

    duration = value.get("duration_minutes")
    interval_count = value.get("interval_count")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0 or duration % 15 != 0:
        raise ApprovalConsumeError("plan duration_minutes must be a positive multiple of 15")
    if not isinstance(interval_count, int) or isinstance(interval_count, bool) or interval_count != duration // 15:
        raise ApprovalConsumeError("plan interval_count does not match duration_minutes")
    if int((ends - starts).total_seconds()) != duration * 60:
        raise ApprovalConsumeError("plan time window does not match duration_minutes")

    power = _finite_number(value.get("power_kw"), "power_kw")
    average = _finite_number(value.get("average_czk_kwh"), "average_czk_kwh")
    minimum = _finite_number(value.get("minimum_czk_kwh"), "minimum_czk_kwh")
    maximum = _finite_number(value.get("maximum_czk_kwh"), "maximum_czk_kwh")
    energy = _finite_number(value.get("estimated_energy_kwh"), "estimated_energy_kwh")
    cost = _finite_number(value.get("estimated_cost_czk"), "estimated_cost_czk")
    if power <= 0:
        raise ApprovalConsumeError("plan power_kw must be positive")
    if minimum > average or average > maximum:
        raise ApprovalConsumeError("plan average price must be within minimum and maximum")
    expected_energy = power * duration / 60
    if not math.isclose(energy, expected_energy, rel_tol=1e-9, abs_tol=1e-9):
        raise ApprovalConsumeError("plan estimated_energy_kwh is inconsistent")
    if not math.isclose(cost, energy * average, rel_tol=1e-9, abs_tol=1e-9):
        raise ApprovalConsumeError("plan estimated_cost_czk is inconsistent")

    return LoadPlan(
        load_id=profile.profile_id,
        name=profile.name,
        starts_at=starts.isoformat(),
        ends_at=ends.isoformat(),
        duration_minutes=duration,
        interval_count=interval_count,
        power_kw=power,
        average_czk_kwh=average,
        minimum_czk_kwh=minimum,
        maximum_czk_kwh=maximum,
        estimated_energy_kwh=energy,
        estimated_cost_czk=cost,
    )


def _validate_existing_attempt_artifact(
    existing: ExecutionAttempt,
    *,
    approval: ExecutionApproval,
    entry_id: str,
    profile_id: str,
) -> None:
    fingerprint = approval_artifact_fingerprint(approval)
    if (
        existing.entry_id != entry_id
        or existing.profile_id != profile_id
        or existing.approval_fingerprint != fingerprint
        or existing.snapshot_digest != approval.snapshot_digest
        or existing.intent != approval.intent
    ):
        raise AttemptConflictError(
            "approval_id already has a persisted attempt with different artifact or scope"
        )


def _snapshot_scope_matches_attempt(
    snapshot: ExecutionActionSnapshot,
    attempt: ExecutionAttempt,
) -> bool:
    snapshot.validated()
    attempt.validated()
    return (
        snapshot.attempt_id == attempt.attempt_id
        and snapshot.entry_id == attempt.entry_id
        and snapshot.profile_id == attempt.profile_id
        and snapshot.entity_id == attempt.entity_id
        and snapshot.approval_id == attempt.approval_id
        and snapshot.approval_fingerprint == attempt.approval_fingerprint
        and snapshot.approval_snapshot_digest == attempt.snapshot_digest
    )


def _validate_snapshot_matches_attempt(
    snapshot: ExecutionActionSnapshot,
    attempt: ExecutionAttempt,
) -> None:
    if not _snapshot_scope_matches_attempt(snapshot, attempt) or snapshot.created_at != attempt.created_at:
        raise ActionSnapshotConflictError(
            "persisted action snapshot does not match the execution attempt"
        )


async def _idempotent_existing_result(
    hass: HomeAssistant,
    existing: ExecutionAttempt,
    *,
    approval: ExecutionApproval,
    entry_id: str,
    profile_id: str,
) -> dict[str, Any]:
    _validate_existing_attempt_artifact(
        existing,
        approval=approval,
        entry_id=entry_id,
        profile_id=profile_id,
    )
    snapshot = await action_snapshot_repository(hass, entry_id).async_get_by_attempt_id(
        existing.attempt_id
    )
    if snapshot is None:
        raise ApprovalConsumeError(
            "persisted execution attempt is missing its immutable action snapshot"
        )
    _validate_snapshot_matches_attempt(snapshot, existing)
    return {
        "attempt": existing.as_dict(),
        "action_snapshot": snapshot.as_dict(),
        "created": False,
        "idempotent_replay": True,
        "action_snapshot_created": False,
        "action_snapshot_idempotent_replay": True,
        "approval_consumed": True,
        "execution_performed": False,
        "service_call_performed": False,
        "executor_available": False,
    }


async def async_consume_execution_approval(
    hass: HomeAssistant,
    *,
    entry_id: str,
    profile_id: str,
    approval_value: Any,
    plan_value: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist action snapshot + attempt, then consume approval, without execution."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ApprovalConsumeError("now must be timezone-aware")
    approval = _approval_from_dict(approval_value)

    async with _transaction_lock(hass, entry_id):
        attempt_repository = _attempt_repository(hass, entry_id)
        existing = await attempt_repository.async_get_by_approval_id(approval.approval_id)
        if existing is not None:
            return await _idempotent_existing_result(
                hass,
                existing,
                approval=approval,
                entry_id=entry_id,
                profile_id=profile_id,
            )

        entry = _entry(hass, entry_id)
        profile = profile_by_id(entry.options, profile_id)
        policy = effective_policy_from_options(entry.options, profile_id)
        plan = _plan_from_snapshot(profile, plan_value, now=current)
        entity_available = _entity_available(hass, profile.entity_id)
        authority = _approval_authority(hass, entry_id)

        verification = authority.verify(
            approval,
            profile,
            plan,
            policy,
            entity_available=entity_available,
            now=current,
        )
        if not verification.valid:
            raise ApprovalConsumeError(f"approval verification failed: {verification.reason}")

        try:
            action_intent = resolve_start_action_intent(profile)
        except UnsupportedActionIntentError as err:
            raise ApprovalConsumeError(f"safe action mapping unavailable: {err}") from err

        current_timestamp = int(current.timestamp())
        attempt = ExecutionAttempt.from_consumed_approval(
            entry_id=entry_id,
            profile_id=profile.profile_id,
            entity_id=profile.entity_id,
            approval=approval,
            created_at=current_timestamp,
        )
        snapshot_repository = action_snapshot_repository(hass, entry_id)
        orphan_snapshot = await snapshot_repository.async_get_by_attempt_id(attempt.attempt_id)
        if orphan_snapshot is not None:
            if not _snapshot_scope_matches_attempt(orphan_snapshot, attempt):
                raise ActionSnapshotConflictError(
                    "orphan action snapshot scope does not match the current approval attempt"
                )
            if orphan_snapshot.created_at > current_timestamp:
                raise ActionSnapshotConflictError(
                    "orphan action snapshot timestamp is in the future"
                )
            attempt = ExecutionAttempt.from_consumed_approval(
                entry_id=entry_id,
                profile_id=profile.profile_id,
                entity_id=profile.entity_id,
                approval=approval,
                created_at=orphan_snapshot.created_at,
            )
            action_snapshot = ExecutionActionSnapshot.from_attempt_and_intent(
                attempt=attempt,
                intent=action_intent,
                created_at=orphan_snapshot.created_at,
            )
        else:
            action_snapshot = ExecutionActionSnapshot.from_attempt_and_intent(
                attempt=attempt,
                intent=action_intent,
                created_at=current_timestamp,
            )

        snapshot_record = await snapshot_repository.async_record(action_snapshot)
        attempt_record = await attempt_repository.async_record(attempt)

        consumed = authority.consume(
            approval,
            profile,
            plan,
            policy,
            entity_available=entity_available,
            now=current,
        )
        if not consumed.valid or not consumed.consumed:
            raise RuntimeError(
                f"approval became invalid after audit persistence: {consumed.reason}"
            )
        return {
            "attempt": attempt_record.attempt.as_dict(),
            "action_snapshot": snapshot_record.snapshot.as_dict(),
            "created": attempt_record.created,
            "idempotent_replay": attempt_record.idempotent_replay,
            "action_snapshot_created": snapshot_record.created,
            "action_snapshot_idempotent_replay": snapshot_record.idempotent_replay,
            "approval_consumed": True,
            "execution_performed": False,
            "service_call_performed": False,
            "executor_available": False,
        }


async def async_list_execution_attempts(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> dict[str, Any]:
    _entry(hass, entry_id)
    attempts = await _attempt_repository(hass, entry_id).async_list()
    return {
        "entry_id": entry_id,
        "attempts": [attempt.as_dict() for attempt in attempts],
        "execution_performed": False,
        "service_call_performed": False,
        "executor_available": False,
    }


@callback
def async_register_load_execution_consume_websocket(hass: HomeAssistant) -> None:
    """Register admin-only consume and attempt-audit commands once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_CONSUME_APPROVAL,
            vol.Required("entry_id"): str,
            vol.Required("profile_id"): str,
            vol.Required("approval"): dict,
            vol.Required("plan"): dict,
        }
    )
    async def websocket_consume(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_consume_execution_approval(
                hass,
                entry_id=msg["entry_id"],
                profile_id=msg["profile_id"],
                approval_value=msg["approval"],
                plan_value=msg["plan"],
            )
        except AttemptConflictError as err:
            connection.send_error(msg["id"], "execution_attempt_conflict", str(err))
            return
        except ActionSnapshotConflictError as err:
            connection.send_error(msg["id"], "execution_action_snapshot_conflict", str(err))
            return
        except ApprovalConsumeError as err:
            connection.send_error(msg["id"], "execution_approval_consume_rejected", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_approval_consume_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    @websocket_api.require_admin
    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_LIST_ATTEMPTS,
            vol.Required("entry_id"): str,
        }
    )
    async def websocket_list_attempts(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        try:
            result = await async_list_execution_attempts(hass, entry_id=msg["entry_id"])
        except ApprovalConsumeError as err:
            connection.send_error(msg["id"], "execution_attempts_invalid", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "execution_attempts_unavailable", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, websocket_consume)
    websocket_api.async_register_command(hass, websocket_list_attempts)
    domain_data[_REGISTERED_KEY] = True
