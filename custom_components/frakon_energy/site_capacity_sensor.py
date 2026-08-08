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
from .site_capacity import SiteCapacitySettings, SiteCapacityStatus, build_site_capacity_status


@dataclass(frozen=True, slots=True)
class SiteCapacitySensorDefinition:
    key: str
    name: str
    status_sensor: bool = False
    snapshot_field: str | None = None


SITE_CAPACITY_DEFINITIONS = (
    SiteCapacitySensorDefinition("grid_capacity_status", "Stav kapacity přívodu", status_sensor=True),
    SiteCapacitySensorDefinition("grid_import_headroom_power", "Rezerva přívodu", snapshot_field="grid_headroom_kw"),
    SiteCapacitySensorDefinition("grid_import_over_limit_power", "Překročení limitu přívodu", snapshot_field="grid_over_limit_kw"),
)


def site_capacity_sensor_definitions(entry: ConfigEntry) -> tuple[SiteCapacitySensorDefinition, ...]:
    settings = SiteCapacitySettings.from_options(entry.options)
    return SITE_CAPACITY_DEFINITIONS if settings.max_grid_import_kw is not None else ()


class FrakonSiteCapacitySensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, definition: SiteCapacitySensorDefinition) -> None:
        self._hass = hass; self._entry = entry; self._definition = definition
        self._attr_name = definition.name
        self._attr_unique_id = f"frakon_energy_{entry.entry_id}_capacity_{definition.key}"
        self._attr_device_info = {"identifiers": {(DOMAIN, f"site_capacity:{entry.entry_id}")}, "name": "FRAKON Energy – Kapacita přívodu", "manufacturer": "FRAKON Energy", "model": "Site grid capacity model"}
        if not definition.status_sensor:
            self._attr_device_class = SensorDeviceClass.POWER; self._attr_state_class = SensorStateClass.MEASUREMENT; self._attr_native_unit_of_measurement = UnitOfPower.KILO_WATT

    def _status(self) -> SiteCapacityStatus:
        return build_site_capacity_status(self._hass, entry_id=self._entry.entry_id, options=self._entry.options)

    @property
    def native_value(self) -> float | str | None:
        status = self._status()
        if self._definition.status_sensor: return status.status
        if self._definition.snapshot_field is None: return None
        return getattr(status, self._definition.snapshot_field)

    @property
    def available(self) -> bool:
        if self._definition.status_sensor: return True
        return self.native_value is not None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        status = self._status()
        return {
            "configured": status.configured, "topology_ready": status.topology_ready, "source_available": status.source_available,
            "max_grid_import_kw": status.max_grid_import_kw, "current_grid_import_kw": status.current_grid_import_kw,
            "grid_headroom_kw": status.grid_headroom_kw, "grid_over_limit_kw": status.grid_over_limit_kw,
            "utilization_percent": status.utilization_percent, "source_entity_id": status.source_entity_id,
            "reason": status.reason, "execution_guard_active": status.execution_guard_active,
            "read_only": True, "service_call_performed": False, "execution_performed": False,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        source = self._status().source_entity_id
        if source:
            self.async_on_remove(async_track_state_change_event(self.hass, [source], self._async_source_state_changed))

    @callback
    def _async_source_state_changed(self, event: Event[EventStateChangedData]) -> None:
        self.async_write_ha_state()


def build_site_capacity_sensors(hass: HomeAssistant, entry: ConfigEntry) -> tuple[FrakonSiteCapacitySensor, ...]:
    return tuple(FrakonSiteCapacitySensor(hass, entry, definition) for definition in site_capacity_sensor_definitions(entry))
