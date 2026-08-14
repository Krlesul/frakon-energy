from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
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
    GRID_CURRENT_L1 = "grid_current_l1"
    GRID_CURRENT_L2 = "grid_current_l2"
    GRID_CURRENT_L3 = "grid_current_l3"


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
        EntityRole.GRID_CURRENT_L1,
        EntityRole.GRID_CURRENT_L2,
        EntityRole.GRID_CURRENT_L3,
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

_PHASE_ROLE_NUMBER = {
    EntityRole.GRID_CURRENT_L1: 1,
    EntityRole.GRID_CURRENT_L2: 2,
    EntityRole.GRID_CURRENT_L3: 3,
}


def _phase_name_matches(text: str, phase: int) -> bool:
    """Require an explicit phase marker; never infer a phase from generic current."""
    patterns = (
        rf"(?:^|[^a-z0-9])l[ _-]?{phase}(?:[^a-z0-9]|$)",
        rf"(?:^|[^a-z0-9])phase[ _-]?{phase}(?:[^a-z0-9]|$)",
        rf"(?:^|[^a-z0-9])faze[ _-]?{phase}(?:[^a-z0-9]|$)",
        rf"(?:^|[^a-z0-9])fáze[ _-]?{phase}(?:[^a-z0-9]|$)",
    )
    return any(re.search(pattern, text) for pattern in patterns)


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
        if entity.device_class == "power":
            score += 35
            reasons.append("power flow device class")
        elif entity.device_class == "energy":
            score += 25
            reasons.append("energy flow device class")
        words = ("import", "odběr", "odber", "grid_in") if role == EntityRole.GRID_IMPORT else ("export", "přetok", "pretok", "grid_out")
        if any(word in text for word in words):
            score += 65
            reasons.append("grid direction name")
    elif role in _PHASE_ROLE_NUMBER:
        phase = _PHASE_ROLE_NUMBER[role]
        # Phase current is safety-relevant. A generic current sensor is never
        # assigned to L1/L2/L3 merely because its unit is amperes.
        if not _phase_name_matches(text, phase):
            return 0, ()
        score += 50
        reasons.append(f"explicit phase {phase} name")
        if entity.device_class == "current":
            score += 30
            reasons.append("device_class current")
        if entity.unit in ("A", "mA"):
            score += 20
            reasons.append("current unit")

    return min(score, 100), tuple(reasons)


def _technology_source_rank(
    technology: HouseTechnology,
    entity: EntityDescriptor,
) -> int:
    """Prefer explicit technology integrations when otherwise equally confident."""
    if technology == HouseTechnology.ELECTRIC_VEHICLE and entity.integration:
        return 1
    return 0


def discover_existing_entities(
    technology: HouseTechnology,
    entities: Iterable[EntityDescriptor],
) -> dict[EntityRole, tuple[EntityMatch, ...]]:
    """Rank existing Home Assistant entities for the selected technology."""
    result: dict[EntityRole, tuple[EntityMatch, ...]] = {}
    for role in ROLE_RULES.get(technology, ()):
        matches: list[tuple[EntityMatch, int]] = []
        for entity in entities:
            score, reasons = _score(entity, role)
            if score >= 50:
                matches.append(
                    (
                        EntityMatch(role, entity.entity_id, score, reasons),
                        _technology_source_rank(technology, entity),
                    )
                )
        result[role] = tuple(
            match
            for match, _source_rank in sorted(
                matches,
                key=lambda item: (
                    -item[0].confidence,
                    -item[1],
                    item[0].entity_id,
                ),
            )
        )
    return result
