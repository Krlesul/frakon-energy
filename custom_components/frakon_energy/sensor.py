from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_PROVIDER, DOMAIN, PROVIDER_CEZ_HDO
from .coordinator import FrakonEnergyCoordinator
from .hdo_coordinator import CezHdoCoordinator
from .providers.cez_hdo import CezHdoSnapshot
from .providers.visionq import VisionQMeasurement


@dataclass(frozen=True, kw_only=True)
class FrakonEnergySensorDescription(SensorEntityDescription):
    value_fn: Callable[[VisionQMeasurement], object]


VISIONQ_SENSORS = (
    FrakonEnergySensorDescription(key="high_rate", name="VT celkem", device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, value_fn=lambda d: d.high_rate_kwh),
    FrakonEnergySensorDescription(key="low_rate", name="NT celkem", device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, value_fn=lambda d: d.low_rate_kwh),
    FrakonEnergySensorDescription(key="total", name="Elektroměr celkem", device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, value_fn=lambda d: d.total_kwh),
    FrakonEnergySensorDescription(key="last_activity", name="Poslední aktivita", device_class=SensorDeviceClass.TIMESTAMP, value_fn=lambda d: datetime.fromtimestamp(d.timestamp).astimezone() if d.timestamp else None),
    FrakonEnergySensorDescription(key="battery_state", name="Stav baterie", device_class=SensorDeviceClass.BATTERY, state_class=SensorStateClass.MEASUREMENT, native_unit_of_measurement=PERCENTAGE, value_fn=lambda d: d.battery_state),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if entry.data.get(CONF_PROVIDER) == PROVIDER_CEZ_HDO:
        async_add_entities(
            CezHdoSensor(coordinator, key, name)
            for key, name in (
                ("tariff", "HDO tarif"),
                ("interval", "HDO aktivní interval"),
                ("next_switch", "HDO další přepnutí"),
                ("countdown", "HDO odpočet"),
                ("today_schedule", "HDO dnešní rozvrh"),
                ("data_valid", "HDO data platná"),
                ("current_price", "HDO aktuální cena"),
            )
        )
        return

    async_add_entities(
        FrakonEnergySensor(coordinator, description)
        for description in VISIONQ_SENSORS
    )


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


class CezHdoSensor(CoordinatorEntity[CezHdoCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: CezHdoCoordinator, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"cez_hdo_{coordinator.source.source_id}_{key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"cez_hdo:{coordinator.source.source_id}")},
            "name": "FRAKON Energy – ČEZ HDO",
            "manufacturer": "FRAKON Energy",
            "model": "ČEZ HDO adapter",
        }
        if key == "next_switch":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif key == "current_price":
            self._attr_device_class = SensorDeviceClass.MONETARY
            self._attr_native_unit_of_measurement = "Kč/kWh"

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.source_available

    @property
    def native_value(self):
        data: CezHdoSnapshot = self.coordinator.data
        if self._key == "tariff":
            return data.tariff
        if self._key == "interval":
            if data.interval_start is None or data.interval_end is None:
                return None
            return f"{data.tariff} {data.interval_start:%H:%M}–{data.interval_end:%H:%M}"
        if self._key == "next_switch":
            return data.next_switch
        if self._key == "countdown":
            if data.countdown_seconds is None:
                return None
            hours, remainder = divmod(data.countdown_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        if self._key == "today_schedule":
            return " | ".join(
                f"{datetime.fromisoformat(item['start']):%H:%M}–{datetime.fromisoformat(item['end']):%H:%M} {item['tariff']}"
                for item in data.today_schedule
            )
        if self._key == "data_valid":
            return data.data_valid
        if self._key == "current_price":
            return data.current_price
        return None

    @property
    def extra_state_attributes(self):
        if self._key != "today_schedule":
            return None
        return {"schedule": list(self.coordinator.data.today_schedule)}
