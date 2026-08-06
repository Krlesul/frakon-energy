from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .entity_assignment import EntityAssignment
from .entity_discovery import EntityRole
from .technology_profile import HouseTechnology

OPTIONS_KEY_ENTITY_ASSIGNMENTS = "entity_assignments"
ENTITY_ASSIGNMENT_STORAGE_VERSION = 1


@dataclass(frozen=True, slots=True)
class EntityAssignmentStorage:
    """Versioned, frontend-safe representation of confirmed entity mappings."""

    assignments: tuple[EntityAssignment, ...] = ()
    version: int = ENTITY_ASSIGNMENT_STORAGE_VERSION

    def as_options_value(self) -> dict[str, object]:
        return {
            "version": self.version,
            "items": [
                {
                    "technology": item.technology.value,
                    "role": item.role.value,
                    "entity_id": item.entity_id,
                    "confirmed": bool(item.confirmed),
                }
                for item in self.assignments
            ],
        }


def _validate_entity_id(entity_id: str) -> str:
    value = entity_id.strip()
    if not value or "." not in value or value.startswith(".") or value.endswith("."):
        raise ValueError("entity assignment must contain a valid Home Assistant entity_id")
    return value


def load_entity_assignment_storage(options: Mapping[str, Any] | None) -> EntityAssignmentStorage:
    """Load confirmed entity mappings from config-entry options.

    Unknown or malformed records are ignored so a single stale mapping cannot prevent
    FRAKON Energy from starting after Home Assistant entity or integration changes.
    """

    if not options:
        return EntityAssignmentStorage()

    raw_storage = options.get(OPTIONS_KEY_ENTITY_ASSIGNMENTS)
    if not isinstance(raw_storage, Mapping):
        return EntityAssignmentStorage()

    raw_version = raw_storage.get("version", ENTITY_ASSIGNMENT_STORAGE_VERSION)
    try:
        version = int(raw_version)
    except (TypeError, ValueError):
        version = ENTITY_ASSIGNMENT_STORAGE_VERSION

    raw_items = raw_storage.get("items", ())
    if not isinstance(raw_items, Iterable) or isinstance(raw_items, (str, bytes, Mapping)):
        raw_items = ()

    assignments: list[EntityAssignment] = []
    seen: set[tuple[HouseTechnology, EntityRole]] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            continue
        try:
            technology = HouseTechnology(str(raw_item.get("technology", "")))
            role = EntityRole(str(raw_item.get("role", "")))
            entity_id = _validate_entity_id(str(raw_item.get("entity_id", "")))
        except (TypeError, ValueError):
            continue

        key = (technology, role)
        if key in seen:
            continue
        seen.add(key)
        assignments.append(
            EntityAssignment(
                technology=technology,
                role=role,
                entity_id=entity_id,
                confirmed=bool(raw_item.get("confirmed", True)),
            )
        )

    return EntityAssignmentStorage(assignments=tuple(assignments), version=version)


def store_entity_assignments(
    options: Mapping[str, Any] | None,
    assignments: Iterable[EntityAssignment],
) -> dict[str, Any]:
    """Return config-entry options with versioned entity mappings replaced atomically."""

    updated = dict(options or {})
    storage = EntityAssignmentStorage(assignments=tuple(assignments))
    updated[OPTIONS_KEY_ENTITY_ASSIGNMENTS] = storage.as_options_value()
    return updated


def remove_stale_entity_assignments(
    assignments: Iterable[EntityAssignment],
    existing_entity_ids: Iterable[str],
) -> tuple[EntityAssignment, ...]:
    """Drop mappings whose source Home Assistant entity no longer exists."""

    existing = set(existing_entity_ids)
    return tuple(item for item in assignments if item.entity_id in existing)
