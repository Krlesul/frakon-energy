"""Strict allowlisted action-intent mapping for FRAKON Energy flexible loads.

This module only describes a future Home Assistant action. It deliberately
contains no service-call execution path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from typing import Any

from .load_profiles import (
    PROFILE_KIND_BATTERY,
    PROFILE_KIND_BOILER,
    PROFILE_KIND_EV,
    PROFILE_KIND_GENERIC,
    LoadProfile,
)

ACTION_START_LOAD = "start_load"
ACTION_STATE_READY = "ready"
ACTION_STATE_ALREADY_SATISFIED = "already_satisfied"
ACTION_STATE_BLOCKED = "blocked"

_SUPPORTED_START_DOMAINS: dict[str, tuple[str, str]] = {
    "switch": ("switch", "turn_on"),
    "input_boolean": ("input_boolean", "turn_on"),
}
_ALLOWED_KINDS_BY_DOMAIN: dict[str, frozenset[str]] = {
    "switch": frozenset({PROFILE_KIND_EV, PROFILE_KIND_BOILER, PROFILE_KIND_GENERIC}),
    "input_boolean": frozenset({PROFILE_KIND_GENERIC}),
}


class UnsupportedActionIntentError(ValueError):
    """Raised when a profile cannot be mapped to a safe fixed action."""


@dataclass(frozen=True, slots=True)
class LoadActionIntent:
    """Immutable description of one future allowlisted start action."""

    intent_id: str
    action: str
    profile_id: str
    profile_kind: str
    entity_id: str
    entity_domain: str
    service_domain: str
    service_name: str
    target: dict[str, str]
    service_data: dict[str, Any]
    desired_state: str
    executor_available: bool = False
    service_call_performed: bool = False

    def validated(self) -> "LoadActionIntent":
        if self.action != ACTION_START_LOAD:
            raise ValueError("unsupported action intent")
        mapping = _SUPPORTED_START_DOMAINS.get(self.entity_domain)
        if mapping is None or mapping != (self.service_domain, self.service_name):
            raise ValueError("service mapping is not allowlisted")
        if self.profile_kind not in _ALLOWED_KINDS_BY_DOMAIN[self.entity_domain]:
            raise ValueError("profile kind is not allowed for entity domain")
        if self.target != {"entity_id": self.entity_id}:
            raise ValueError("action target must contain only the bound entity_id")
        if self.service_data != {}:
            raise ValueError("start action does not accept arbitrary service data")
        if self.desired_state != "on":
            raise ValueError("start action desired state must be on")
        if self.executor_available or self.service_call_performed:
            raise ValueError("action intent cannot represent execution")
        return self

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ActionStateDecision:
    """Read-only current-state decision for a fixed action intent."""

    status: str
    reason: str
    current_state: str | None
    desired_state: str
    service_call_performed: bool = False
    executor_available: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _entity_domain(entity_id: str) -> str:
    domain, separator, object_id = entity_id.partition(".")
    if not separator or not domain or not object_id:
        raise UnsupportedActionIntentError("profile entity_id is invalid")
    return domain


def resolve_start_action_intent(profile: LoadProfile) -> LoadActionIntent:
    """Map one enabled profile to a fixed allowlisted start intent.

    No caller-provided service/domain/data values are accepted by this API.
    """
    profile.validated()
    if not profile.enabled:
        raise UnsupportedActionIntentError("disabled profile cannot produce an action intent")
    if not profile.entity_id:
        raise UnsupportedActionIntentError("profile requires an entity binding")
    if profile.kind == PROFILE_KIND_BATTERY:
        raise UnsupportedActionIntentError("battery start action is not safely defined")

    entity_domain = _entity_domain(profile.entity_id)
    mapping = _SUPPORTED_START_DOMAINS.get(entity_domain)
    if mapping is None:
        raise UnsupportedActionIntentError(f"unsupported entity domain: {entity_domain}")
    allowed_kinds = _ALLOWED_KINDS_BY_DOMAIN[entity_domain]
    if profile.kind not in allowed_kinds:
        raise UnsupportedActionIntentError(
            f"profile kind {profile.kind} is not allowed for {entity_domain}"
        )

    service_domain, service_name = mapping
    identity = f"{ACTION_START_LOAD}\0{profile.profile_id}\0{profile.kind}\0{profile.entity_id}"
    intent_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return LoadActionIntent(
        intent_id=intent_id,
        action=ACTION_START_LOAD,
        profile_id=profile.profile_id,
        profile_kind=profile.kind,
        entity_id=profile.entity_id,
        entity_domain=entity_domain,
        service_domain=service_domain,
        service_name=service_name,
        target={"entity_id": profile.entity_id},
        service_data={},
        desired_state="on",
    ).validated()


def evaluate_action_current_state(
    intent: LoadActionIntent,
    current_state: str | None,
) -> ActionStateDecision:
    """Evaluate whether a fixed start intent is ready without calling a service."""
    intent.validated()
    normalized = current_state.strip().lower() if isinstance(current_state, str) else None
    if normalized == intent.desired_state:
        return ActionStateDecision(
            status=ACTION_STATE_ALREADY_SATISFIED,
            reason="entity_already_in_desired_state",
            current_state=normalized,
            desired_state=intent.desired_state,
        )
    if normalized == "off":
        return ActionStateDecision(
            status=ACTION_STATE_READY,
            reason="entity_state_allows_start",
            current_state=normalized,
            desired_state=intent.desired_state,
        )
    if normalized in (None, "unknown", "unavailable"):
        return ActionStateDecision(
            status=ACTION_STATE_BLOCKED,
            reason="entity_state_unavailable",
            current_state=normalized,
            desired_state=intent.desired_state,
        )
    return ActionStateDecision(
        status=ACTION_STATE_BLOCKED,
        reason="entity_state_not_allowlisted_for_start",
        current_state=normalized,
        desired_state=intent.desired_state,
    )
