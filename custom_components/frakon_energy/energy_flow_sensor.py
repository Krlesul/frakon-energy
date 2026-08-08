from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN
from .energy_flow_model import EnergyFlowSnapshot, build_energy_flow_snapshot


ENERGY_FLOW_SENSOR_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("house_load_kw", "Spotřeba domu"),
    ("pv_generation_kw", "Výroba FVE"),
    ("grid_import_kw", "Odběr ze sítě"),
    ("grid_export_kw", "Přetok do sítě"),
    ("battery_charge_kw", "Nabíjení baterie"),
    ("battery_discharge_kw", "Vybíjení baterie"),
    ("known_load_kw", "Známá podružná spotřeba"),
)


class FrakonEnergyFlowSensor(SensorEntity):
    """Read-only native HA sensor backed by the authoritative energy-flow model."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, key: str, name: str) -> None:
        self._hass = hass
        self._entry = entry
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"frakon_energy_{entry.entry_id}_flow_{key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"energy_flow:{entry.entry_id}")},
            "name": "FRAKON Energy – Toky energie",
            "manufacturer": "FRAKON Energy",
            "model": "Authoritative energy-flow model",
        }

    def _snapshot(self) -> EnergyFlowSnapshot:
        return build_energy_flow_snapshot(
            self._hass,
            entry_id=self._entry.entry_id,
            options=self._entry.options,
        )

    @property
    def native_value(self) -> float | None:
        return getattr(self._snapshot(), self._key)

    @property
    def available(self) -> bool:
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
        if self._key == "house_load_kw":
            attributes["formula"] = "PV + grid import + battery discharge - grid export - battery charge"
        return attributes

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        snapshot = self._snapshot()
        source_entity_ids = sorted(
            {
                reading.entity_id
                for reading in snapshot.entities.values()
                if reading.entity_id is not None
            }
        )
        if source_entity_ids:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    source_entity_ids,
                    self._async_source_state_changed,
                )
            )

    @callback
    def _async_source_state_changed(self, event: Event[Any]) -> None:
        self.async_write_ha_state()


def build_energy_flow_sensors(hass: HomeAssistant, entry: ConfigEntry) -> tuple[FrakonEnergyFlowSensor, ...]:
    return tuple(
        FrakonEnergyFlowSensor(hass, entry, key, name)
        for key, name in ENERGY_FLOW_SENSOR_DEFINITIONS
    )
