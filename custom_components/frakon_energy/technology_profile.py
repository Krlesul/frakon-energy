from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class HouseTechnology(StrEnum):
    PHOTOVOLTAICS = "photovoltaics"
    HOME_BATTERY = "home_battery"
    ELECTRIC_VEHICLE = "electric_vehicle"
    WALLBOX = "wallbox"
    HEAT_PUMP = "heat_pump"
    ELECTRIC_BOILER = "electric_boiler"
    HOT_WATER_TANK = "hot_water_tank"
    ELECTRIC_HEATING = "electric_heating"
    GAS_HEATING = "gas_heating"
    SOLID_FUEL_HEATING = "solid_fuel_heating"
    CHP = "chp"
    GENERATOR = "generator"
    SMART_METER = "smart_meter"
    SUBMETERS = "submeters"
    DYNAMIC_TARIFF = "dynamic_tariff"
    HDO = "hdo"
    ENERGY_EXPORT = "energy_export"


TECHNOLOGY_LABELS_CS: dict[HouseTechnology, str] = {
    HouseTechnology.PHOTOVOLTAICS: "Fotovoltaická elektrárna",
    HouseTechnology.HOME_BATTERY: "Domácí baterie",
    HouseTechnology.ELECTRIC_VEHICLE: "Elektromobil",
    HouseTechnology.WALLBOX: "Wallbox",
    HouseTechnology.HEAT_PUMP: "Tepelné čerpadlo",
    HouseTechnology.ELECTRIC_BOILER: "Elektrický bojler",
    HouseTechnology.HOT_WATER_TANK: "Akumulační nádrž / ohřev vody",
    HouseTechnology.ELECTRIC_HEATING: "Elektrické vytápění",
    HouseTechnology.GAS_HEATING: "Plynové vytápění",
    HouseTechnology.SOLID_FUEL_HEATING: "Kotel na pevná paliva",
    HouseTechnology.CHP: "Kogenerační jednotka",
    HouseTechnology.GENERATOR: "Záložní generátor",
    HouseTechnology.SMART_METER: "Chytrý elektroměr",
    HouseTechnology.SUBMETERS: "Podružné elektroměry",
    HouseTechnology.DYNAMIC_TARIFF: "Spotový / dynamický tarif",
    HouseTechnology.HDO: "HDO a nízký tarif",
    HouseTechnology.ENERGY_EXPORT: "Přetoky do sítě",
}


@dataclass(frozen=True, slots=True)
class TechnologySelection:
    technology: HouseTechnology
    enabled: bool = False
    entity_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if len(self.entity_ids) != len(set(self.entity_ids)):
            raise ValueError("technology entity ids must be unique")
        if any(not entity_id or "." not in entity_id for entity_id in self.entity_ids):
            raise ValueError("technology entity ids must be valid Home Assistant entity ids")


@dataclass(frozen=True, slots=True)
class HouseTechnologyProfile:
    technologies: tuple[TechnologySelection, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        ids = [item.technology for item in self.technologies]
        if len(ids) != len(set(ids)):
            raise ValueError("each house technology may be configured only once")

    def enabled(self) -> tuple[TechnologySelection, ...]:
        return tuple(item for item in self.technologies if item.enabled)

    def has(self, technology: HouseTechnology) -> bool:
        return any(item.technology == technology and item.enabled for item in self.technologies)

    def visible_modules(self) -> tuple[str, ...]:
        modules: list[str] = ["grid", "consumption", "billing"]
        mapping: tuple[tuple[HouseTechnology, str], ...] = (
            (HouseTechnology.PHOTOVOLTAICS, "photovoltaics"),
            (HouseTechnology.HOME_BATTERY, "battery"),
            (HouseTechnology.ELECTRIC_VEHICLE, "electric_vehicle"),
            (HouseTechnology.WALLBOX, "wallbox"),
            (HouseTechnology.HEAT_PUMP, "heat_pump"),
            (HouseTechnology.ELECTRIC_BOILER, "hot_water"),
            (HouseTechnology.HOT_WATER_TANK, "hot_water"),
            (HouseTechnology.GENERATOR, "generator"),
            (HouseTechnology.HDO, "hdo"),
            (HouseTechnology.DYNAMIC_TARIFF, "dynamic_tariff"),
            (HouseTechnology.ENERGY_EXPORT, "export"),
            (HouseTechnology.SUBMETERS, "submeters"),
        )
        for technology, module in mapping:
            if self.has(technology) and module not in modules:
                modules.append(module)
        return tuple(modules)


def default_technology_profile() -> HouseTechnologyProfile:
    return HouseTechnologyProfile(
        technologies=tuple(
            TechnologySelection(technology=technology, enabled=False)
            for technology in HouseTechnology
        )
    )


def technology_profile_payload(profile: HouseTechnologyProfile) -> dict[str, object]:
    return {
        "technologies": [
            {
                "id": item.technology.value,
                "label": TECHNOLOGY_LABELS_CS[item.technology],
                "enabled": item.enabled,
                "entity_ids": list(item.entity_ids),
            }
            for item in profile.technologies
        ],
        "enabled": [item.technology.value for item in profile.enabled()],
        "visible_modules": list(profile.visible_modules()),
    }
