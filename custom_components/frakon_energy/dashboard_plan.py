from __future__ import annotations

from dataclasses import dataclass

from .technology_profile import HouseTechnology, HouseTechnologyProfile


@dataclass(frozen=True, slots=True)
class DashboardModule:
    id: str
    title_cs: str
    category: str
    required_technologies: tuple[HouseTechnology, ...] = ()

    def is_available(self, profile: HouseTechnologyProfile) -> bool:
        return all(profile.has(technology) for technology in self.required_technologies)


MODULE_CATALOG: tuple[DashboardModule, ...] = (
    DashboardModule("energy_summary", "Energetický přehled", "core"),
    DashboardModule("current_tariff", "Aktuální tarif", "core"),
    DashboardModule("billing", "Vyúčtování", "core"),
    DashboardModule("hdo_timeline", "HDO plán", "tariff", (HouseTechnology.HDO,)),
    DashboardModule("dynamic_prices", "Dynamické ceny", "tariff", (HouseTechnology.DYNAMIC_TARIFF,)),
    DashboardModule("photovoltaics", "Fotovoltaika", "production", (HouseTechnology.PHOTOVOLTAICS,)),
    DashboardModule("energy_export", "Přetoky do sítě", "production", (HouseTechnology.PHOTOVOLTAICS, HouseTechnology.ENERGY_EXPORT)),
    DashboardModule("home_battery", "Domácí baterie", "storage", (HouseTechnology.HOME_BATTERY,)),
    DashboardModule("electric_vehicle", "Elektromobil", "mobility", (HouseTechnology.ELECTRIC_VEHICLE,)),
    DashboardModule("wallbox", "Wallbox", "mobility", (HouseTechnology.WALLBOX,)),
    DashboardModule("smart_charging", "Chytré nabíjení", "mobility", (HouseTechnology.ELECTRIC_VEHICLE, HouseTechnology.WALLBOX)),
    DashboardModule("heat_pump", "Tepelné čerpadlo", "heating", (HouseTechnology.HEAT_PUMP,)),
    DashboardModule("hot_water", "Ohřev vody", "heating", (HouseTechnology.ELECTRIC_BOILER,)),
    DashboardModule("hot_water_storage", "Akumulace teplé vody", "heating", (HouseTechnology.HOT_WATER_TANK,)),
    DashboardModule("electric_heating", "Elektrické vytápění", "heating", (HouseTechnology.ELECTRIC_HEATING,)),
    DashboardModule("gas_heating", "Plynové vytápění", "heating", (HouseTechnology.GAS_HEATING,)),
    DashboardModule("solid_fuel_heating", "Kotel na pevná paliva", "heating", (HouseTechnology.SOLID_FUEL_HEATING,)),
    DashboardModule("generator", "Záložní generátor", "backup", (HouseTechnology.GENERATOR,)),
    DashboardModule("submeters", "Podružná měření", "metering", (HouseTechnology.SUBMETERS,)),
)


def available_dashboard_modules(profile: HouseTechnologyProfile) -> tuple[DashboardModule, ...]:
    """Return only dashboard modules supported by the selected house technologies."""

    return tuple(module for module in MODULE_CATALOG if module.is_available(profile))


def dashboard_plan_payload(profile: HouseTechnologyProfile) -> dict[str, object]:
    modules = available_dashboard_modules(profile)
    return {
        "modules": [
            {
                "id": module.id,
                "title": module.title_cs,
                "category": module.category,
                "required_technologies": [technology.value for technology in module.required_technologies],
            }
            for module in modules
        ],
        "module_ids": [module.id for module in modules],
        "categories": list(dict.fromkeys(module.category for module in modules)),
    }
