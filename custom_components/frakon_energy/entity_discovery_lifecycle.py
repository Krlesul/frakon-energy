from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .entity_discovery_runtime import EntityDiscoveryRuntime


@dataclass(slots=True)
class EntityDiscoveryRuntimeRegistry:
    """Track entity-discovery runtimes by Home Assistant config-entry id."""

    _items: dict[str, EntityDiscoveryRuntime] = field(default_factory=dict)

    def register(self, entry_id: str, runtime: EntityDiscoveryRuntime) -> None:
        if not entry_id:
            raise ValueError("entry_id is required")
        self._items[entry_id] = runtime

    def get(self, entry_id: str) -> EntityDiscoveryRuntime:
        try:
            return self._items[entry_id]
        except KeyError as err:
            raise KeyError(
                f"entity discovery runtime is not registered for {entry_id}"
            ) from err

    def remove(self, entry_id: str) -> EntityDiscoveryRuntime | None:
        return self._items.pop(entry_id, None)

    def as_frontend_summary(self) -> dict[str, Any]:
        return {
            "entry_ids": sorted(self._items),
            "count": len(self._items),
        }
