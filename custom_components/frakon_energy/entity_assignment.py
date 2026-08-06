from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .entity_discovery import (
    EntityDescriptor,
    EntityMatch,
    EntityRole,
    discover_existing_entities,
)
from .technology_profile import HouseTechnology, HouseTechnologyProfile


ROLE_LABELS_CS: dict[EntityRole, str] = {
    EntityRole.BATTERY_LEVEL: "Stav baterie",
    EntityRole.RANGE: "Dojezd",
    EntityRole.POWER: "Aktuální výkon",
    EntityRole.ENERGY_TOTAL: "Celková energie",
    EntityRole.CHARGING_STATE: "Stav nabíjení",
    EntityRole.CHARGE_LIMIT: "Limit nabití",
    EntityRole.PV_POWER: "Výkon fotovoltaiky",
    EntityRole.GRID_IMPORT: "Odběr ze sítě",
    EntityRole.GRID_EXPORT: "Přetok do sítě",
}


@dataclass(frozen=True, slots=True)
class EntityAssignment:
    technology: HouseTechnology
    role: EntityRole
    entity_id: str
    confirmed: bool = False


@dataclass(frozen=True, slots=True)
class TechnologyDiscoveryResult:
    technology: HouseTechnology
    recommendations: Mapping[EntityRole, tuple[EntityMatch, ...]] = field(default_factory=dict)
    assignments: tuple[EntityAssignment, ...] = field(default_factory=tuple)

    def assigned_entity(self, role: EntityRole) -> str | None:
        for assignment in self.assignments:
            if assignment.role == role:
                return assignment.entity_id
        return None


def build_discovery_results(
    profile: HouseTechnologyProfile,
    entities: Iterable[EntityDescriptor],
    assignments: Iterable[EntityAssignment] = (),
) -> tuple[TechnologyDiscoveryResult, ...]:
    """Build discovery results only for enabled technologies.

    Existing physical Home Assistant entities are recommended and reused. FRAKON
    creates its own entities only for derived calculations, forecasts and costs.
    """

    entity_list = tuple(entities)
    assignment_list = tuple(assignments)
    results: list[TechnologyDiscoveryResult] = []
    for selection in profile.enabled():
        technology_assignments = tuple(
            item for item in assignment_list if item.technology == selection.technology
        )
        results.append(
            TechnologyDiscoveryResult(
                technology=selection.technology,
                recommendations=discover_existing_entities(selection.technology, entity_list),
                assignments=technology_assignments,
            )
        )
    return tuple(results)


def discovery_payload(results: Iterable[TechnologyDiscoveryResult]) -> dict[str, object]:
    technologies: list[dict[str, object]] = []
    for result in results:
        role_items: list[dict[str, object]] = []
        for role, matches in result.recommendations.items():
            selected = result.assigned_entity(role)
            role_items.append(
                {
                    "role": role.value,
                    "label": ROLE_LABELS_CS[role],
                    "selected_entity_id": selected,
                    "configured": selected is not None,
                    "candidates": [
                        {
                            "entity_id": match.entity_id,
                            "confidence": match.confidence,
                            "requires_confirmation": match.requires_confirmation,
                            "reasons": list(match.reasons),
                        }
                        for match in matches
                    ],
                }
            )
        configured = sum(1 for item in role_items if item["configured"])
        technologies.append(
            {
                "technology": result.technology.value,
                "roles": role_items,
                "configured_roles": configured,
                "total_roles": len(role_items),
                "complete": configured == len(role_items) and bool(role_items),
            }
        )
    return {"technologies": technologies}
