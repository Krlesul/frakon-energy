from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_PROVIDER, DOMAIN, PROVIDER_CEZ_HDO, PROVIDER_VISIONQ
from .coordinator import FrakonEnergyCoordinator
from .entity_discovery_lifecycle import EntityDiscoveryRuntimeRegistry
from .entity_discovery_setup import setup_entity_discovery_runtime, unload_entity_discovery_runtime
from .entity_discovery_ws_api import async_register_entity_discovery_websocket
from .ha_entity_registry_adapter import registry_records_from_home_assistant
from .hdo_coordinator import CezHdoCoordinator
from .load_execution_action_snapshot_ws_api import async_register_load_execution_action_snapshot_websocket
from .load_execution_approval_ws_api import async_register_load_execution_approval_preview_websocket
from .load_execution_bounded_dispatch_gate_ws_api import async_register_load_execution_bounded_dispatch_gate_websocket
from .load_execution_consume_ws_api import async_register_load_execution_consume_websocket
from .load_execution_dispatch_gate_ws_api import async_register_load_execution_dispatch_gate_websocket
from .load_execution_lifecycle_recovery import async_initialize_lifecycle_recovery
from .load_execution_lifecycle_recovery_ws_api import async_register_load_execution_lifecycle_recovery_websocket
from .load_execution_lifecycle_ws_api import async_register_load_execution_lifecycle_websocket
from .load_execution_noop_completion_ws_api import async_register_load_execution_noop_completion_websocket
from .load_execution_policy_ws_api import async_register_load_execution_policy_websocket
from .load_execution_readiness_ws_api import async_register_load_execution_readiness_websocket
from .load_execution_recovery_resolution_ws_api import async_register_load_execution_recovery_resolution_websocket
from .load_execution_recovery_verification_ws_api import async_register_load_execution_recovery_verification_websocket
from .load_execution_start_dispatcher_ws_api import async_register_load_execution_start_dispatcher_websocket
from .load_execution_stop_dispatcher_ws_api import async_register_load_execution_stop_dispatcher_websocket
from .load_execution_stop_due_ws_api import async_register_load_execution_stop_due_websocket
from .load_execution_stop_lease_ws_api import async_register_load_execution_stop_lease_websocket
from .load_execution_stop_recovery import async_initialize_stop_recovery
from .load_execution_stop_resolution_ws_api import async_register_load_execution_stop_resolution_websocket
from .load_execution_stop_scheduler import async_start_stop_scheduler, async_stop_stop_scheduler
from .load_execution_stop_scheduler_ws_api import async_register_load_execution_stop_scheduler_websocket
from .load_plan_ws_api import async_register_load_plan_websocket
from .load_profiles_ws_api import async_register_load_profiles_websocket
from .panel import async_register_panel
from .providers.visionq import VisionQApiClient
from .spot_price_settings_ws_api import async_register_spot_price_settings_websocket
from .spot_price_ws_api import async_register_spot_price_websocket
from .technology_profile_options import technology_profile_from_options
from .technology_profile_ws_api import async_register_technology_profile_websocket

_ENTITY_DISCOVERY_REGISTRY = "entity_discovery_runtime_registry"


def _runtime_registry(hass: HomeAssistant) -> EntityDiscoveryRuntimeRegistry:
    domain_data = hass.data.setdefault(DOMAIN, {})
    registry = domain_data.get(_ENTITY_DISCOVERY_REGISTRY)
    if isinstance(registry, EntityDiscoveryRuntimeRegistry):
        return registry
    registry = EntityDiscoveryRuntimeRegistry()
    domain_data[_ENTITY_DISCOVERY_REGISTRY] = registry
    return registry


def _entity_registry_snapshot(hass: HomeAssistant) -> tuple[Any, ...]:
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    states = {state.entity_id: state for state in hass.states.async_all()}
    device_names = {device.id: device.name_by_user or device.name or device.model or device.id for device in device_registry.devices.values()}
    return registry_records_from_home_assistant(entity_registry.entities.values(), states=states, device_names=device_names)


def _update_entry_options(hass: HomeAssistant, entry: ConfigEntry, options: Mapping[str, Any]) -> None:
    hass.config_entries.async_update_entry(entry, options=dict(options))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    provider = entry.data.get(CONF_PROVIDER, PROVIDER_VISIONQ)
    if provider == PROVIDER_CEZ_HDO:
        coordinator = CezHdoCoordinator(hass, entry)
    else:
        client = VisionQApiClient(async_get_clientsession(hass), entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
        coordinator = FrakonEnergyCoordinator(hass, entry, client)
        await coordinator.async_initialize_history()
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    runtime_registry = _runtime_registry(hass)
    setup_entity_discovery_runtime(entry_id=entry.entry_id, runtime_registry=runtime_registry, profile_provider=lambda: technology_profile_from_options(entry.options), registry_provider=lambda: _entity_registry_snapshot(hass), options_provider=lambda: entry.options, options_updater=lambda options: _update_entry_options(hass, entry, options))
    await async_initialize_lifecycle_recovery(hass, entry_id=entry.entry_id)
    await async_initialize_stop_recovery(hass, entry_id=entry.entry_id)
    await async_start_stop_scheduler(hass, entry.entry_id)
    async_register_entity_discovery_websocket(hass, runtime_registry)
    async_register_technology_profile_websocket(hass)
    async_register_spot_price_websocket(hass)
    async_register_spot_price_settings_websocket(hass)
    async_register_load_plan_websocket(hass)
    async_register_load_profiles_websocket(hass)
    async_register_load_execution_policy_websocket(hass)
    async_register_load_execution_approval_preview_websocket(hass)
    async_register_load_execution_consume_websocket(hass)
    async_register_load_execution_action_snapshot_websocket(hass)
    async_register_load_execution_readiness_websocket(hass)
    async_register_load_execution_lifecycle_websocket(hass)
    async_register_load_execution_lifecycle_recovery_websocket(hass)
    async_register_load_execution_recovery_resolution_websocket(hass)
    async_register_load_execution_recovery_verification_websocket(hass)
    async_register_load_execution_dispatch_gate_websocket(hass)
    async_register_load_execution_noop_completion_websocket(hass)
    async_register_load_execution_stop_lease_websocket(hass)
    async_register_load_execution_bounded_dispatch_gate_websocket(hass)
    async_register_load_execution_stop_due_websocket(hass)
    async_register_load_execution_stop_resolution_websocket(hass)
    async_register_load_execution_stop_scheduler_websocket(hass)
    async_register_load_execution_stop_dispatcher_websocket(hass)
    async_register_load_execution_start_dispatcher_websocket(hass)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    await async_register_panel(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    if unloaded:
        await async_stop_stop_scheduler(hass, entry.entry_id)
        unload_entity_discovery_runtime(entry_id=entry.entry_id, runtime_registry=_runtime_registry(hass))
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
