from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping

from homeassistant.core import HomeAssistant

from .energy_flow_settings import (
    CONF_BATTERY_POWER_SIGN,
    CONF_EV_WALLBOX_RELATION,
    CONF_GRID_METER_SCOPE,
    CONF_PV_POWER_SCOPE,
    flow_settings_from_options,
)
from .entity_assignment_storage import load_entity_assignment_storage
from .entity_discovery import EntityRole
from .technology_profile import HouseTechnology
from .technology_profile_options import technology_profile_from_options

QUALITY_COMPLETE = "complete"
QUALITY_PARTIAL = "partial"
QUALITY_NEEDS_SETUP = "needs_setup"


@dataclass(frozen=True, slots=True)
class PowerReading:
    entity_id: str | None
    value_kw: float | None
    state: str | None
    unit: str | None
    available: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EnergyFlowSnapshot:
    entry_id: str
    quality: str
    quality_label: str
    reasons: tuple[str, ...]
    house_load_kw: float | None
    pv_generation_kw: float | None
    grid_import_kw: float | None
    grid_export_kw: float | None
    battery_charge_kw: float | None
    battery_discharge_kw: float | None
    known_load_kw: float | None
    known_load_quality: str
    known_load_reason: str
    topology: Mapping[str, str]
    entities: Mapping[str, PowerReading]
    read_only: bool = True
    service_call_performed: bool = False
    execution_performed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "quality": self.quality,
            "quality_label": self.quality_label,
            "reasons": list(self.reasons),
            "house_load_kw": self.house_load_kw,
            "pv_generation_kw": self.pv_generation_kw,
            "grid_import_kw": self.grid_import_kw,
            "grid_export_kw": self.grid_export_kw,
            "battery_charge_kw": self.battery_charge_kw,
            "battery_discharge_kw": self.battery_discharge_kw,
            "known_load_kw": self.known_load_kw,
            "known_load_quality": self.known_load_quality,
            "known_load_reason": self.known_load_reason,
            "topology": dict(self.topology),
            "entities": {key: value.as_dict() for key, value in self.entities.items()},
            "read_only": self.read_only,
            "service_call_performed": self.service_call_performed,
            "execution_performed": self.execution_performed,
        }


def _enabled_technologies(options: Mapping[str, Any]) -> set[HouseTechnology]:
    return {item.technology for item in technology_profile_from_options(options).enabled()}


def _assignment_map(
    options: Mapping[str, Any],
) -> dict[tuple[HouseTechnology, EntityRole], str]:
    enabled = _enabled_technologies(options)
    return {
        (item.technology, item.role): item.entity_id
        for item in load_entity_assignment_storage(options).assignments
        if item.confirmed and item.technology in enabled
    }


def _first_assignment(
    assignments: Mapping[tuple[HouseTechnology, EntityRole], str],
    candidates: tuple[tuple[HouseTechnology, EntityRole], ...],
) -> str | None:
    for candidate in candidates:
        entity_id = assignments.get(candidate)
        if entity_id:
            return entity_id
    return None


def _power_reading(hass: HomeAssistant, entity_id: str | None) -> PowerReading:
    if not entity_id:
        return PowerReading(None, None, None, None, False, "entity_not_configured")
    state = hass.states.get(entity_id)
    if state is None:
        return PowerReading(entity_id, None, None, None, False, "entity_missing")
    raw_state = str(state.state).strip()
    unit = str(state.attributes.get("unit_of_measurement", "")).strip() or None
    if raw_state.lower() in {"unknown", "unavailable", "none", ""}:
        return PowerReading(entity_id, None, raw_state, unit, False, "entity_unavailable")
    try:
        value = float(raw_state.replace(",", "."))
    except ValueError:
        return PowerReading(entity_id, None, raw_state, unit, False, "state_not_numeric")
    if not math.isfinite(value):
        return PowerReading(entity_id, None, raw_state, unit, False, "state_not_finite")
    if unit == "W":
        value_kw = value / 1000.0
    elif unit == "kW":
        value_kw = value
    elif unit == "MW":
        value_kw = value * 1000.0
    else:
        return PowerReading(entity_id, None, raw_state, unit, False, "unsupported_power_unit")
    return PowerReading(entity_id, value_kw, raw_state, unit, True, "ok")


def _quality_label(quality: str) -> str:
    if quality == QUALITY_COMPLETE:
        return "Kompletní měření"
    if quality == QUALITY_PARTIAL:
        return "Částečné měření"
    return "Vyžaduje nastavení"


def _known_loads(
    readings: Mapping[str, PowerReading],
    *,
    ev_wallbox_relation: str,
) -> tuple[float | None, str, str]:
    load_keys = (
        "wallbox",
        "ev",
        "heat_pump",
        "electric_boiler",
        "hot_water_tank",
        "electric_heating",
        "submeters",
    )
    configured = [key for key in load_keys if readings[key].entity_id]
    if not configured:
        return None, QUALITY_PARTIAL, "Žádné podružné výkonové měření není potvrzené."

    ev_configured = readings["ev"].entity_id is not None
    wallbox_configured = readings["wallbox"].entity_id is not None
    relation_ambiguous = ev_configured and wallbox_configured and ev_wallbox_relation == "unknown"

    exclude = {"ev"} if ev_wallbox_relation == "same_flow" else set()
    seen_entities: set[str] = set()
    total = 0.0
    available_count = 0
    unavailable_count = 0
    for key in load_keys:
        if key in exclude:
            continue
        reading = readings[key]
        if reading.entity_id is None:
            continue
        if reading.entity_id in seen_entities:
            continue
        seen_entities.add(reading.entity_id)
        if reading.value_kw is None:
            unavailable_count += 1
            continue
        total += abs(reading.value_kw)
        available_count += 1

    value = total if available_count else None
    if relation_ambiguous:
        return value, QUALITY_PARTIAL, "Vztah EV a wallboxu není potvrzený; rozpad spotřeby může obsahovat dvojí měření."
    if unavailable_count:
        return value, QUALITY_PARTIAL, "Některé potvrzené podružné výkonové entity právě nemají použitelná data."
    return value, QUALITY_COMPLETE, "Potvrzené podružné výkony jsou sečtené bez duplicitních entity_id."


def build_energy_flow_snapshot(
    hass: HomeAssistant,
    *,
    entry_id: str,
    options: Mapping[str, Any],
) -> EnergyFlowSnapshot:
    """Build a read-only fail-closed energy-flow snapshot from confirmed HA mappings."""
    if not entry_id:
        raise ValueError("entry_id is required")

    settings = flow_settings_from_options(options)
    enabled = _enabled_technologies(options)
    assignments = _assignment_map(options)

    entity_ids = {
        "pv": _first_assignment(
            assignments,
            ((HouseTechnology.PHOTOVOLTAICS, EntityRole.PV_POWER),),
        ),
        "grid_import": _first_assignment(
            assignments,
            (
                (HouseTechnology.SMART_METER, EntityRole.GRID_IMPORT),
                (HouseTechnology.PHOTOVOLTAICS, EntityRole.GRID_IMPORT),
            ),
        ),
        "grid_export": _first_assignment(
            assignments,
            (
                (HouseTechnology.SMART_METER, EntityRole.GRID_EXPORT),
                (HouseTechnology.ENERGY_EXPORT, EntityRole.GRID_EXPORT),
                (HouseTechnology.PHOTOVOLTAICS, EntityRole.GRID_EXPORT),
            ),
        ),
        "battery": _first_assignment(
            assignments,
            ((HouseTechnology.HOME_BATTERY, EntityRole.POWER),),
        ),
        "wallbox": _first_assignment(
            assignments,
            ((HouseTechnology.WALLBOX, EntityRole.POWER),),
        ),
        "ev": _first_assignment(
            assignments,
            ((HouseTechnology.ELECTRIC_VEHICLE, EntityRole.POWER),),
        ),
        "heat_pump": _first_assignment(
            assignments,
            ((HouseTechnology.HEAT_PUMP, EntityRole.POWER),),
        ),
        "electric_boiler": _first_assignment(
            assignments,
            ((HouseTechnology.ELECTRIC_BOILER, EntityRole.POWER),),
        ),
        "hot_water_tank": _first_assignment(
            assignments,
            ((HouseTechnology.HOT_WATER_TANK, EntityRole.POWER),),
        ),
        "electric_heating": _first_assignment(
            assignments,
            ((HouseTechnology.ELECTRIC_HEATING, EntityRole.POWER),),
        ),
        "submeters": _first_assignment(
            assignments,
            ((HouseTechnology.SUBMETERS, EntityRole.POWER),),
        ),
    }
    readings = {key: _power_reading(hass, entity_id) for key, entity_id in entity_ids.items()}

    reasons: list[str] = []
    setup_blocked = False
    data_blocked = False

    if settings[CONF_GRID_METER_SCOPE] != "whole_house":
        setup_blocked = True
        reasons.append("Hlavní elektroměr není potvrzený jako měření celého domu.")
    if settings[CONF_PV_POWER_SCOPE] != "gross_generation":
        setup_blocked = True
        reasons.append("Výkon FVE není potvrzený jako hrubá AC výroba.")

    for key, label in (
        ("pv", "FVE"),
        ("grid_import", "odběr ze sítě"),
        ("grid_export", "přetok do sítě"),
    ):
        reading = readings[key]
        if reading.entity_id is None:
            setup_blocked = True
            reasons.append(f"Chybí potvrzená výkonová entita: {label}.")
        elif reading.value_kw is None:
            data_blocked = True
            reasons.append(f"Výkonová entita {label} právě nemá použitelná data ({reading.reason}).")

    battery_enabled = HouseTechnology.HOME_BATTERY in enabled
    if battery_enabled:
        battery = readings["battery"]
        if settings[CONF_BATTERY_POWER_SIGN] == "unknown":
            setup_blocked = True
            reasons.append("Je aktivní domácí baterie, ale není potvrzen význam znaménka jejího výkonu.")
        if battery.entity_id is None:
            setup_blocked = True
            reasons.append("Je aktivní domácí baterie, ale chybí potvrzená výkonová entita baterie.")
        elif battery.value_kw is None:
            data_blocked = True
            reasons.append(f"Výkon baterie právě není použitelný ({battery.reason}).")

    house_load_kw: float | None = None
    battery_charge_kw: float | None = None
    battery_discharge_kw: float | None = None
    if not setup_blocked and not data_blocked:
        pv = abs(readings["pv"].value_kw or 0.0)
        grid_import = abs(readings["grid_import"].value_kw or 0.0)
        grid_export = abs(readings["grid_export"].value_kw or 0.0)
        battery_value = readings["battery"].value_kw if battery_enabled else None
        charge = 0.0
        discharge = 0.0
        if battery_value is not None:
            positive_is_charge = settings[CONF_BATTERY_POWER_SIGN] == "positive_is_charge"
            charging = (battery_value > 0) if positive_is_charge else (battery_value < 0)
            if abs(battery_value) > 0.03:
                if charging:
                    charge = abs(battery_value)
                else:
                    discharge = abs(battery_value)
            battery_charge_kw = charge
            battery_discharge_kw = discharge
        house_load_kw = max(0.0, pv + grid_import + discharge - grid_export - charge)

    known_load_kw, known_load_quality, known_load_reason = _known_loads(
        readings,
        ev_wallbox_relation=settings[CONF_EV_WALLBOX_RELATION],
    )

    if setup_blocked:
        quality = QUALITY_NEEDS_SETUP
    elif data_blocked:
        quality = QUALITY_PARTIAL
    else:
        quality = QUALITY_COMPLETE
        if known_load_quality == QUALITY_PARTIAL and (
            readings["ev"].entity_id is not None and readings["wallbox"].entity_id is not None
        ):
            quality = QUALITY_PARTIAL
            reasons.append(known_load_reason)

    if not reasons:
        reasons.append("Energetická bilance používá potvrzenou topologii a aktuální Home Assistant výkonové entity.")

    return EnergyFlowSnapshot(
        entry_id=entry_id,
        quality=quality,
        quality_label=_quality_label(quality),
        reasons=tuple(reasons),
        house_load_kw=house_load_kw,
        pv_generation_kw=(abs(readings["pv"].value_kw) if readings["pv"].value_kw is not None else None),
        grid_import_kw=(abs(readings["grid_import"].value_kw) if readings["grid_import"].value_kw is not None else None),
        grid_export_kw=(abs(readings["grid_export"].value_kw) if readings["grid_export"].value_kw is not None else None),
        battery_charge_kw=battery_charge_kw,
        battery_discharge_kw=battery_discharge_kw,
        known_load_kw=known_load_kw,
        known_load_quality=known_load_quality,
        known_load_reason=known_load_reason,
        topology=settings,
        entities=readings,
    )
