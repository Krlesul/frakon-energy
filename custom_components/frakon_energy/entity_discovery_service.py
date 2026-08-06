from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .entity_assignment import EntityAssignment, build_discovery_results, discovery_payload
from .ha_entity_registry import RegistryEntityRecord, discovery_descriptors_from_registry
from .technology_profile import HouseTechnologyProfile


@dataclass(frozen=True, slots=True)
class EntityDiscoverySnapshot:
    """Frontend-safe snapshot of reusable Home Assistant entity recommendations."""

    payload: dict[str, object]
    scanned_entities: int
    usable_entities: int

    def as_dict(self) -> dict[str, object]:
        return {
            **self.payload,
            "scanned_entities": self.scanned_entities,
            "usable_entities": self.usable_entities,
        }


def build_runtime_entity_discovery_snapshot(
    *,
    profile: HouseTechnologyProfile,
    registry_records: Iterable[RegistryEntityRecord],
    assignments: Iterable[EntityAssignment] = (),
    include_unavailable: bool = False,
) -> EntityDiscoverySnapshot:
    """Build entity recommendations from the current Home Assistant registry state.

    Existing physical entities are reused. Disabled and hidden entities are always
    excluded; unavailable entities are excluded unless diagnostic mode requests them.
    """

    records = tuple(registry_records)
    descriptors = discovery_descriptors_from_registry(
        records,
        include_unavailable=include_unavailable,
    )
    results = build_discovery_results(profile, descriptors, assignments)
    return EntityDiscoverySnapshot(
        payload=discovery_payload(results),
        scanned_entities=len(records),
        usable_entities=len(descriptors),
    )
