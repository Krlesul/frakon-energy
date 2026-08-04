from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FrakonEnergyCoordinator
from .providers.visionq import VisionQMeasurement


@dataclass(frozen=True, kw_only=True)
class FrakonEnergySensorDescription(SensorEntityDescription):
    value_fn: Callable[[VisionQMeasurement], object]


SENSORS = (
    FrakonEnergySensorDescription(key="high_rate", name="VT celkem", device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, value_fn=lambda d: d.high_rate_kwh),
    FrakonEnergySensorDescription(key="low_rate", name="NT celkem", device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, value_fn=lambda d: d.low_rate_kwh),
    FrakonEnergySensorDescription(key="total", name="Elektroměr celkem", device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, value_fn=lambda d: d.total_kwh),
    FrakonEnergySensorDescription(key="last_activity", name="Poslední aktivita", device_class=SensorDeviceClass.TIMESTAMP, value_fn=lambda d: datetime.fromtimestamp(d.timestamp).astimezone() if d.timestamp else None),
    FrakonEnergySensorDescription(key="battery_state", name="Stav baterie", device_class=SensorDeviceClass.BATTERY, state_class=SensorStateClass.MEASUREMENT, native_unit_of_measurement=PERCENTAGE, value_fn=lambda d: d.battery_state),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator: FrakonEnergyCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(FrakonEnergySensor(coordinator, description) for description in SENSORS)


class FrakonEnergySensor(CoordinatorEntity[FrakonEnergyCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: FrakonEnergyCoordinator, description: FrakonEnergySensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"visionq_{coordinator.eui}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"visionq:{coordinator.eui}")},
            "name": f"FRAKON Energy – VisionQ {coordinator.eui}",
            "manufacturer": "VisionQ",
            "model": "ElIoT Energy Monitor",
        }

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.coordinator.data)
