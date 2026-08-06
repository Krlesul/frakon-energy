from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from .entity_assignment import EntityAssignment
from .entity_assignment_storage import (
    load_entity_assignment_storage,
    store_entity_assignments,
)
from .entity_discovery_service import build_runtime_entity_discovery_snapshot
from .entity_discovery_websocket import (
    COMMAND_GET,
    COMMAND_REMOVE,
    COMMAND_RESCAN,
    COMMAND_SAVE,
    remove_assignment,
    save_assignment,
)
from .ha_entity_registry import RegistryEntityRecord
from .technology_profile import HouseTechnologyProfile


@dataclass(slots=True)
class EntityDiscoveryRuntime:
    """Runtime coordinator used by Home Assistant WebSocket handlers."""

    profile_provider: Callable[[], HouseTechnologyProfile]
    registry_provider: Callable[[], Iterable[RegistryEntityRecord]]
    options_provider: Callable[[], Mapping[str, Any]]
    options_updater: Callable[[Mapping[str, Any]], None]

    def snapshot(self, *, include_unavailable: bool = False) -> dict[str, object]:
        storage = load_entity_assignment_storage(self.options_provider())
        return build_runtime_entity_discovery_snapshot(
            profile=self.profile_provider(),
            registry_records=tuple(self.registry_provider()),
            assignments=storage.assignments,
            include_unavailable=include_unavailable,
        ).as_dict()

    def save(self, payload: Mapping[str, Any]) -> dict[str, object]:
        storage = load_entity_assignment_storage(self.options_provider())
        assignments = save_assignment(storage.assignments, payload)
        self.options_updater(store_entity_assignments(self.options_provider(), assignments))
        return self.snapshot()

    def remove(self, payload: Mapping[str, Any]) -> dict[str, object]:
        storage = load_entity_assignment_storage(self.options_provider())
        assignments = remove_assignment(storage.assignments, payload)
        self.options_updater(store_entity_assignments(self.options_provider(), assignments))
        return self.snapshot()

    def dispatch(
        self,
        command: str,
        payload: Mapping[str, Any] | None = None,
        *,
        is_admin: bool = False,
    ) -> dict[str, object]:
        data = payload or {}
        if command == COMMAND_GET:
            return self.snapshot(
                include_unavailable=bool(data.get("include_unavailable", False))
            )
        if command == COMMAND_RESCAN:
            if not is_admin:
                raise PermissionError("administrator privileges required")
            return self.snapshot(
                include_unavailable=bool(data.get("include_unavailable", False))
            )
        if command == COMMAND_SAVE:
            if not is_admin:
                raise PermissionError("administrator privileges required")
            return self.save(data)
        if command == COMMAND_REMOVE:
            if not is_admin:
                raise PermissionError("administrator privileges required")
            return self.remove(data)
        raise ValueError(f"unsupported entity discovery command: {command}")
