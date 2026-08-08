from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN
from .energy_flow_model import EnergyFlowSnapshot, build_energy_flow_snapshot
from .site_capacity_sensor import build_site_capacity_sensors
from .technology_profile import HouseTechnology
from .technology_profile_options import technology_profile_from_options


@dataclass(frozen=True, slots=True)
class EnergyFlowSensorDefinition:
    key: str
    name: str
    snapshot_field: str | None = None
    quality_sensor: bool = False


BASE_DEFINITIONS = (
    EnergyFlowSensorDefinition("house_load_power", "Spotřeba domu", "house_load_kw"),
    EnergyFlowSensorDefinition("pv_generation_power", "Výroba FVE", "pv_generation_kw"),
    EnergyFlowSensorDefinition("grid_import_power", "Odběr ze sítě", "grid_import_kw"),
    EnergyFlowSensorDefinition("grid_export_power", "Přetok do sítě", "grid_export_kw"),
    EnergyFlowSensorDefinition("quality", "Kvalita energetického toku", quality_sensor=True),
)

BATTERY_DEFINITIONS = (
    EnergyFlowSensorDefinition("battery_charge_power", "Nabíjení baterie", "battery_charge_kw"),
    EnergyFlowSensorDefinition("battery_discharge_power", "Vybíjení baterie", "battery_discharge_kw"),
)

KNOWN_LOAD_DEFINITION = EnergyFlowSensorDefinition(
    "known_load_power",
    "Známá podružná spotřeba",
    "known_load_kw",
)

KNOWN_LOAD_TECHNOLOGIES = {
    HouseTechnology.WALLBOX,
    HouseTechnology.ELECTRIC_VEHICLE,
    HouseTechnology.HEAT_PUMP,
    HouseTechnology.ELECTRIC_BOILER,
    HouseTechnology.HOT_WATER_TANK,
    HouseTechnology.ELECTRIC_HEATING,
    HouseTechnology.SUBMETERS,
}


def energy_flow_sensor_definitions(entry: ConfigEntry) -> tuple[EnergyFlowSensorDefinition, ...]:
    """Return only the derived entities relevant to the configured house profile."""
    enabled = {item.technology for item in technology_profile_from_options(entry.options).enabled()}
    definitions = list(BASE_DEFINITIONS)
    if HouseTechnology.HOME_BATTERY in enabled:
        definitions.extend(BATTERY_DEFINITIONS)
    if enabled & KNOWN_LOAD_TECHNOLOGIES:
        definitions.append(KNOWN_LOAD_DEFINITION)
    return tuple(definitions)


class FrakonEnergyFlowSensor(SensorEntity):
    """Read-only native HA sensor backed by the authoritative energy-flow model."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        definition: EnergyFlowSensorDefinition,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._definition = definition
        self._attr_name = definition.name
        self._attr_unique_id = f"frakon_energy_{entry.entry_id}_flow_{definition.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"energy_flow:{entry.entry_id}")},
            "name": "FRAKON Energy – Toky energie",
            "manufacturer": "FRAKON Energy",
            "model": "Authoritative energy-flow model",
        }
        if not definition.quality_sensor:
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_native_unit_of_measurement = UnitOfPower.KILO_WATT

    def _snapshot(self) -> EnergyFlowSnapshot:
        return build_energy_flow_snapshot(
            self._hass,
            entry_id=self._entry.entry_id,
            options=self._entry.options,
        )

    def _source_entity_ids(self) -> tuple[str, ...]:
        snapshot = self._snapshot()
        return tuple(
            dict.fromkeys(
                reading.entity_id
                for reading in snapshot.entities.values()
                if reading.entity_id is not None
            )
        )

    @property
    def native_value(self) -> float | str | None:
        snapshot = self._snapshot()
        if self._definition.quality_sensor:
            return snapshot.quality
        if self._definition.snapshot_field is None:
            return None
        return getattr(snapshot, self._definition.snapshot_field)

    @property
    def available(self) -> bool:
        if self._definition.quality_sensor:
            return True
        return self.native_value is not None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        snapshot = self._snapshot()
        attributes: dict[str, Any] = {
            "quality": snapshot.quality,
            "quality_label": snapshot.quality_label,
            "reasons": list(snapshot.reasons),
            "known_load_quality": snapshot.known_load_quality,
            "known_load_reason": snapshot.known_load_reason,
            "topology": dict(snapshot.topology),
            "read_only": True,
            "service_call_performed": False,
            "execution_performed": False,
        }
        if self._definition.key == "house_load_power":
            attributes["formula"] = "PV + grid import + battery discharge - grid export - battery charge"
        if self._definition.quality_sensor:
            attributes["house_load_kw"] = snapshot.house_load_kw
            attributes["source_entities"] = {
                key: reading.entity_id for key, reading in snapshot.entities.items()
            }
        return attributes

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        source_entity_ids = self._source_entity_ids()
        if source_entity_ids:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    source_entity_ids,
                    self._async_source_state_changed,
                )
            )

    @callback
    def _async_source_state_changed(self, event: Event[EventStateChangedData]) -> None:
        self.async_write_ha_state()


def build_energy_flow_sensors(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> tuple[SensorEntity, ...]:
    flow_sensors: tuple[SensorEntity, ...] = tuple(
        FrakonEnergyFlowSensor(hass, entry, definition)
        for definition in energy_flow_sensor_definitions(entry)
    )
    return flow_sensors + build_site_capacity_sensors(hass, entry)
