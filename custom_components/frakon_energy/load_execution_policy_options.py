"""Persistent execution-policy settings for FRAKON Energy load profiles."""

from __future__ import annotations

from typing import Any, Mapping

from .load_execution_policy import (
    EXECUTION_MODE_DISABLED,
    LoadExecutionPolicy,
)

OPTION_LOAD_EXECUTION_POLICIES = "load_execution_policies"


def policy_from_dict(value: Mapping[str, Any]) -> LoadExecutionPolicy:
    """Parse and validate one persisted execution policy."""
    raw_power = value.get("max_power_kw")
    raw_duration = value.get("max_duration_minutes")
    return LoadExecutionPolicy(
        profile_id=str(value.get("profile_id", "")),
        mode=str(value.get("mode", EXECUTION_MODE_DISABLED)),
        max_power_kw=None if raw_power is None else float(raw_power),
        max_duration_minutes=None if raw_duration is None else int(raw_duration),
        require_entity_binding=bool(value.get("require_entity_binding", True)),
        require_entity_available=bool(value.get("require_entity_available", True)),
    ).validated()


def policies_from_options(options: Mapping[str, Any]) -> tuple[LoadExecutionPolicy, ...]:
    """Load validated policies from config-entry options."""
    raw = options.get(OPTION_LOAD_EXECUTION_POLICIES, [])
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("load_execution_policies must be a list")

    policies: list[LoadExecutionPolicy] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("each load execution policy must be an object")
        policy = policy_from_dict(item)
        if policy.profile_id in seen:
            raise ValueError(f"duplicate execution policy profile_id: {policy.profile_id}")
        seen.add(policy.profile_id)
        policies.append(policy)
    return tuple(policies)


def policy_by_profile_id(options: Mapping[str, Any], profile_id: str) -> LoadExecutionPolicy:
    """Return persisted policy or a fail-closed disabled default."""
    for policy in policies_from_options(options):
        if policy.profile_id == profile_id:
            return policy
    return LoadExecutionPolicy(profile_id=profile_id, mode=EXECUTION_MODE_DISABLED).validated()


def upsert_policy(options: Mapping[str, Any], policy: LoadExecutionPolicy) -> dict[str, Any]:
    """Return options with one policy inserted or replaced."""
    policy.validated()
    policies = list(policies_from_options(options))
    for index, existing in enumerate(policies):
        if existing.profile_id == policy.profile_id:
            policies[index] = policy
            break
    else:
        policies.append(policy)
    updated = dict(options)
    updated[OPTION_LOAD_EXECUTION_POLICIES] = [item.as_dict() for item in policies]
    return updated


def delete_policy(options: Mapping[str, Any], profile_id: str, *, missing_ok: bool = False) -> dict[str, Any]:
    """Return options without one policy; missing policy may be treated as no-op."""
    policies = list(policies_from_options(options))
    found = any(item.profile_id == profile_id for item in policies)
    if not found and not missing_ok:
        raise ValueError(f"load execution policy not found: {profile_id}")
    updated = dict(options)
    updated[OPTION_LOAD_EXECUTION_POLICIES] = [item.as_dict() for item in policies if item.profile_id != profile_id]
    return updated
