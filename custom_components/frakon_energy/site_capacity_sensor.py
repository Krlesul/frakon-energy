from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfPower
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN
from .site_capacity import SiteCapacityStatus, build_site_capacity_status


@dataclass(frozen=True, slots=True)
class SiteCapacitySensorDefinition:
    key: str
    name: str
    snapshot_field: str
    percentage: bool = False
    text: bool = False


SITE_CAPACITY_SENSOR_DEFINITIONS = (
    SiteCapacitySensorDefinition("status", "Stav kapacity přípojky", "status", text=True),
    SiteCapacitySensorDefinition("grid_import", "Aktuální odběr přípojky", "current_grid_import_kw"),
    SiteCapacitySensorDefinition("grid_limit", "Limit odběru přípojky", "max_grid_import_kw"),
    SiteCapacitySensorDefinition("grid_headroom", "Volná rezerva přípojky", "grid_headroom_kw"),
    SiteCapacitySensorDefinition("grid_over_limit", "Překročení limitu přípojky", "grid_over_limit_kw"),
    SiteCapacitySensorDefinition("grid_utilization", "Využití kapacity přípojky", "utilization_percent", percentage=True),
)


class FrakonSiteCapacitySensor(SensorEntity):
    """Read-only native sensor backed by the authoritative site-capacity model."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        definition: SiteCapacitySensorDefinition,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._definition = definition
        self._attr_name = definition.name
        self._attr_unique_id = f"frakon_energy_{entry.entry_id}_site_capacity_{definition.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"site_capacity:{entry.entry_id}")},
            "name": "FRAKON Energy – Kapacita přípojky",
            "manufacturer": "FRAKON Energy",
            "model": "Read-only site capacity model",
        }
        if definition.percentage:
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_native_unit_of_measurement = PERCENTAGE
        elif not definition.text:
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_native_unit_of_measurement = UnitOfPower.KILO_WATT

    def _snapshot(self) -> SiteCapacityStatus:
        return build_site_capacity_status(
            self._hass,
            entry_id=self._entry.entry_id,
            options=self._entry.options,
        )

    @property
    def native_value(self) -> float | str | None:
        return getattr(self._snapshot(), self._definition.snapshot_field)

    @property
    def available(self) -> bool:
        if self._definition.text:
            return True
        return self.native_value is not None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        snapshot = self._snapshot()
        return {
            "status": snapshot.status,
            "configured": snapshot.configured,
            "topology_ready": snapshot.topology_ready,
            "source_available": snapshot.source_available,
            "source_entity_id": snapshot.source_entity_id,
            "reason": snapshot.reason,
            "read_only": snapshot.read_only,
            "service_call_performed": snapshot.service_call_performed,
            "execution_performed": snapshot.execution_performed,
            "execution_guard_active": snapshot.execution_guard_active,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        source_entity_id = self._snapshot().source_entity_id
        if source_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    (source_entity_id,),
                    self._async_source_state_changed,
                )
            )

    @callback
    def _async_source_state_changed(self, event: Event[EventStateChangedData]) -> None:
        self.async_write_ha_state()


def build_site_capacity_sensors(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> tuple[FrakonSiteCapacitySensor, ...]:
    return tuple(
        FrakonSiteCapacitySensor(hass, entry, definition)
        for definition in SITE_CAPACITY_SENSOR_DEFINITIONS
    )
