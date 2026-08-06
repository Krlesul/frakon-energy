from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .entity_assignment import EntityAssignment
from .entity_discovery import EntityRole
from .technology_profile import HouseTechnology

WS_GET_ENTITY_DISCOVERY = "frakon_energy/entity_discovery/get"
WS_SAVE_ENTITY_ASSIGNMENT = "frakon_energy/entity_discovery/save"
WS_REMOVE_ENTITY_ASSIGNMENT = "frakon_energy/entity_discovery/remove"
WS_RESCAN_ENTITY_DISCOVERY = "frakon_energy/entity_discovery/rescan"


@dataclass(slots=True)
class EntityAssignmentStore:
    """In-memory assignment contract used by the Home Assistant WebSocket layer.

    Runtime registration will persist the same payload in the FRAKON Energy config
    entry/options storage. This model keeps validation and replacement semantics
    independent from Home Assistant imports and therefore directly testable.
    """

    assignments: list[EntityAssignment] = field(default_factory=list)

    def all(self) -> tuple[EntityAssignment, ...]:
        return tuple(self.assignments)

    def save(
        self,
        *,
        technology: HouseTechnology | str,
        role: EntityRole | str,
        entity_id: str,
    ) -> EntityAssignment:
        technology_value = HouseTechnology(technology)
        role_value = EntityRole(role)
        if "." not in entity_id:
            raise ValueError("entity_id must be a valid Home Assistant entity id")

        assignment = EntityAssignment(
            technology=technology_value,
            role=role_value,
            entity_id=entity_id,
            confirmed=True,
        )
        self.assignments = [
            item
            for item in self.assignments
            if not (item.technology == technology_value and item.role == role_value)
        ]
        self.assignments.append(assignment)
        return assignment

    def remove(
        self,
        *,
        technology: HouseTechnology | str,
        role: EntityRole | str,
    ) -> bool:
        technology_value = HouseTechnology(technology)
        role_value = EntityRole(role)
        previous = len(self.assignments)
        self.assignments = [
            item
            for item in self.assignments
            if not (item.technology == technology_value and item.role == role_value)
        ]
        return len(self.assignments) != previous


def assignment_payload(assignment: EntityAssignment) -> dict[str, object]:
    return {
        "technology": assignment.technology.value,
        "role": assignment.role.value,
        "entity_id": assignment.entity_id,
        "confirmed": assignment.confirmed,
    }


def assignments_payload(assignments: Iterable[EntityAssignment]) -> list[dict[str, object]]:
    return [assignment_payload(item) for item in assignments]


def websocket_command_contract() -> tuple[dict[str, object], ...]:
    """Describe the stable frontend/backend command contract."""

    return (
        {
            "type": WS_GET_ENTITY_DISCOVERY,
            "admin_required": False,
            "mutates": False,
            "optional_fields": ["include_unavailable"],
        },
        {
            "type": WS_SAVE_ENTITY_ASSIGNMENT,
            "admin_required": True,
            "mutates": True,
            "required_fields": ["technology", "role", "entity_id"],
        },
        {
            "type": WS_REMOVE_ENTITY_ASSIGNMENT,
            "admin_required": True,
            "mutates": True,
            "required_fields": ["technology", "role"],
        },
        {
            "type": WS_RESCAN_ENTITY_DISCOVERY,
            "admin_required": True,
            "mutates": False,
            "optional_fields": ["include_unavailable"],
        },
    )
