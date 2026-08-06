from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .entity_assignment import EntityAssignment
from .entity_discovery import EntityRole
from .technology_profile import HouseTechnology

COMMAND_GET = "frakon_energy/entity_discovery/get"
COMMAND_RESCAN = "frakon_energy/entity_discovery/rescan"
COMMAND_SAVE = "frakon_energy/entity_discovery/save"
COMMAND_REMOVE = "frakon_energy/entity_discovery/remove"


def _technology(value: Any) -> HouseTechnology:
    return value if isinstance(value, HouseTechnology) else HouseTechnology(str(value))


def _role(value: Any) -> EntityRole:
    return value if isinstance(value, EntityRole) else EntityRole(str(value))


def _entity_id(value: Any) -> str:
    entity_id = str(value).strip()
    if not entity_id or "." not in entity_id or entity_id.startswith(".") or entity_id.endswith("."):
        raise ValueError("entity_id must be a valid Home Assistant entity id")
    return entity_id


def save_assignment(
    assignments: Iterable[EntityAssignment],
    payload: Mapping[str, Any],
) -> tuple[EntityAssignment, ...]:
    """Replace the mapping for one technology/role and preserve all others."""

    technology = _technology(payload.get("technology"))
    role = _role(payload.get("role"))
    assignment = EntityAssignment(
        technology=technology,
        role=role,
        entity_id=_entity_id(payload.get("entity_id")),
        confirmed=True,
    )
    kept = tuple(
        item
        for item in assignments
        if not (item.technology == technology and item.role == role)
    )
    return (*kept, assignment)


def remove_assignment(
    assignments: Iterable[EntityAssignment],
    payload: Mapping[str, Any],
) -> tuple[EntityAssignment, ...]:
    """Remove only the mapping identified by technology and role."""

    technology = _technology(payload.get("technology"))
    role = _role(payload.get("role"))
    return tuple(
        item
        for item in assignments
        if not (item.technology == technology and item.role == role)
    )
