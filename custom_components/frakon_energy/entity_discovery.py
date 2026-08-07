from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from .technology_profile import HouseTechnology


class EntityRole(StrEnum):
    BATTERY_LEVEL = "battery_level"
    RANGE = "range"
    POWER = "power"
    ENERGY_TOTAL = "energy_total"
    CHARGING_STATE = "charging_state"
    CHARGE_LIMIT = "charge_limit"
    PV_POWER = "pv_power"
    GRID_IMPORT = "grid_import"
    GRID_EXPORT = "grid_export"


@dataclass(frozen=True, slots=True)
class EntityDescriptor:
    entity_id: str
    name: str = ""
    device_name: str = ""
    integration: str = ""
    domain: str = "sensor"
    device_class: str | None = None
    state_class: str | None = None
    unit: str | None = None

    def __post_init__(self) -> None:
        if "." not in self.entity_id:
            raise ValueError("invalid Home Assistant entity id")


@dataclass(frozen=True, slots=True)
class EntityMatch:
    role: EntityRole
    entity_id: str
    confidence: int
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 100:
            raise ValueError("confidence must be between 0 and 100")

    @property
    def requires_confirmation(self) -> bool:
        return self.confidence < 90


ROLE_RULES: dict[HouseTechnology, tuple[EntityRole, ...]] = {
    HouseTechnology.ELECTRIC_VEHICLE: (
        EntityRole.BATTERY_LEVEL,
        EntityRole.RANGE,
        EntityRole.CHARGING_STATE,
        EntityRole.POWER,
        EntityRole.CHARGE_LIMIT,
    ),
    HouseTechnology.WALLBOX: (
        EntityRole.POWER,
        EntityRole.ENERGY_TOTAL,
        EntityRole.CHARGING_STATE,
    ),
    HouseTechnology.PHOTOVOLTAICS: (
        EntityRole.PV_POWER,
        EntityRole.ENERGY_TOTAL,
        EntityRole.GRID_IMPORT,
        EntityRole.GRID_EXPORT,
    ),
    HouseTechnology.HOME_BATTERY: (
        EntityRole.BATTERY_LEVEL,
        EntityRole.POWER,
        EntityRole.ENERGY_TOTAL,
    ),
    HouseTechnology.HEAT_PUMP: (
        EntityRole.POWER,
        EntityRole.ENERGY_TOTAL,
    ),
    HouseTechnology.ELECTRIC_BOILER: (
        EntityRole.POWER,
        EntityRole.ENERGY_TOTAL,
    ),
    HouseTechnology.HOT_WATER_TANK: (
        EntityRole.POWER,
        EntityRole.ENERGY_TOTAL,
    ),
    HouseTechnology.ELECTRIC_HEATING: (
        EntityRole.POWER,
        EntityRole.ENERGY_TOTAL,
    ),
    HouseTechnology.CHP: (
        EntityRole.POWER,
        EntityRole.ENERGY_TOTAL,
    ),
    HouseTechnology.GENERATOR: (
        EntityRole.POWER,
        EntityRole.ENERGY_TOTAL,
    ),
    HouseTechnology.SMART_METER: (
        EntityRole.GRID_IMPORT,
        EntityRole.GRID_EXPORT,
        EntityRole.ENERGY_TOTAL,
    ),
    HouseTechnology.SUBMETERS: (
        EntityRole.POWER,
        EntityRole.ENERGY_TOTAL,
    ),
    HouseTechnology.ENERGY_EXPORT: (
        EntityRole.GRID_EXPORT,
        EntityRole.ENERGY_TOTAL,
    ),
}


def _score(entity: EntityDescriptor, role: EntityRole) -> tuple[int, tuple[str, ...]]:
    text = " ".join((entity.entity_id, entity.name, entity.device_name, entity.integration)).lower()
    score = 0
    reasons: list[str] = []

    if role == EntityRole.BATTERY_LEVEL:
        if entity.device_class == "battery":
            score += 55
            reasons.append("device_class battery")
        if entity.unit == "%":
            score += 25
            reasons.append("unit percent")
        if any(word in text for word in ("soc", "battery", "baterie")):
            score += 20
            reasons.append("battery name")
    elif role in (EntityRole.POWER, EntityRole.PV_POWER):
        if entity.device_class == "power":
            score += 55
            reasons.append("device_class power")
        if entity.unit in ("W", "kW"):
            score += 25
            reasons.append("power unit")
        keywords = ("pv", "solar", "výroba") if role == EntityRole.PV_POWER else ("power", "výkon", "charging", "příkon", "prikon")
        if any(word in text for word in keywords):
            score += 20
            reasons.append("matching name")
    elif role == EntityRole.ENERGY_TOTAL:
        if entity.device_class == "energy":
            score += 50
            reasons.append("device_class energy")
        if entity.state_class == "total_increasing":
            score += 30
            reasons.append("state_class total_increasing")
        if entity.unit in ("Wh", "kWh", "MWh"):
            score += 20
            reasons.append("energy unit")
    elif role == EntityRole.RANGE:
        if entity.device_class == "distance":
            score += 55
            reasons.append("device_class distance")
        if entity.unit in ("km", "mi"):
            score += 25
            reasons.append("distance unit")
        if any(word in text for word in ("range", "dojezd")):
            score += 20
            reasons.append("range name")
    elif role == EntityRole.CHARGING_STATE:
        if entity.domain == "binary_sensor":
            score += 35
            reasons.append("binary sensor")
        if any(word in text for word in ("charging", "nabij", "charge_state")):
            score += 65
            reasons.append("charging name")
    elif role == EntityRole.CHARGE_LIMIT:
        if entity.domain == "number":
            score += 35
            reasons.append("number entity")
        if entity.unit == "%":
            score += 25
            reasons.append("unit percent")
        if any(word in text for word in ("limit", "target", "cil")):
            score += 40
            reasons.append("limit name")
    elif role in (EntityRole.GRID_IMPORT, EntityRole.GRID_EXPORT):
        if entity.device_class in ("power", "energy"):
            score += 35
            reasons.append("energy flow device class")
        words = ("import", "odběr", "odber", "grid_in") if role == EntityRole.GRID_IMPORT else ("export", "přetok", "pretok", "grid_out")
        if any(word in text for word in words):
            score += 65
            reasons.append("grid direction name")

    return min(score, 100), tuple(reasons)


def discover_existing_entities(
    technology: HouseTechnology,
    entities: Iterable[EntityDescriptor],
) -> dict[EntityRole, tuple[EntityMatch, ...]]:
    """Rank existing Home Assistant entities for the selected technology.

    FRAKON reuses confirmed physical entities. It creates separate entities only for
    its own derived calculations, forecasts and costs.
    """

    result: dict[EntityRole, tuple[EntityMatch, ...]] = {}
    for role in ROLE_RULES.get(technology, ()):
        matches: list[EntityMatch] = []
        for entity in entities:
            score, reasons = _score(entity, role)
            if score >= 50:
                matches.append(EntityMatch(role, entity.entity_id, score, reasons))
        result[role] = tuple(sorted(matches, key=lambda item: (-item.confidence, item.entity_id)))
    return result
