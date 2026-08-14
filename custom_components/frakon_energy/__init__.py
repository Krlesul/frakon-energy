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
from .energy_flow_ws_api import async_register_energy_flow_websocket
from .entity_discovery_lifecycle import EntityDiscoveryRuntimeRegistry
from .entity_discovery_setup import setup_entity_discovery_runtime, unload_entity_discovery_runtime
from .entity_discovery_ws_api import async_register_entity_discovery_websocket
from .ha_entity_registry_adapter import registry_records_from_home_assistant
from .hdo_coordinator import CezHdoCoordinator
from .load_execution_action_snapshot_ws_api import async_register_load_execution_action_snapshot_websocket
from .load_execution_approval_ws_api import async_register_load_execution_approval_preview_websocket
from .load_execution_arm_ws_api import async_register_load_execution_arm_websocket
from .load_execution_bounded_dispatch_gate_ws_api import async_register_load_execution_bounded_dispatch_gate_websocket
from .load_execution_commissioning_preflight_ws_api import async_register_load_execution_commissioning_preflight_websocket
from .load_execution_consume_ws_api import async_register_load_execution_consume_websocket
from .load_execution_dispatch_gate_ws_api import async_register_load_execution_dispatch_gate_websocket
from .load_execution_lifecycle_recovery import async_initialize_lifecycle_recovery
from .load_execution_lifecycle_recovery_ws_api import async_register_load_execution_lifecycle_recovery_websocket
from .load_execution_lifecycle_ws_api import async_register_load_execution_lifecycle_websocket
from .load_execution_noop_completion_ws_api import async_register_load_execution_noop_completion_websocket
from .load_execution_pending_run_scheduler_ws_api import async_register_load_execution_pending_run_scheduler_websocket
from .load_execution_pending_run_ws_api import async_register_load_execution_pending_run_websocket
from .load_execution_policy_ws_api import async_register_load_execution_policy_websocket
from .load_execution_readiness_ws_api import async_register_load_execution_readiness_websocket
from .load_execution_recovery_resolution_ws_api import async_register_load_execution_recovery_resolution_websocket
from .load_execution_recovery_verification_ws_api import async_register_load_execution_recovery_verification_websocket
from .load_execution_runtime_lifecycle import (
    async_start_execution_runtimes,
    async_stop_execution_runtimes,
)
from .load_execution_safety_status_ws_api import async_register_load_execution_safety_status_websocket
from .load_execution_start_dispatcher_ws_api import async_register_load_execution_start_dispatcher_websocket
from .load_execution_start_scheduler_ws_api import async_register_load_execution_start_scheduler_websocket
from .load_execution_stop_dispatcher_ws_api import async_register_load_execution_stop_dispatcher_websocket
from .load_execution_stop_due_ws_api import async_register_load_execution_stop_due_websocket
from .load_execution_stop_lease_ws_api import async_register_load_execution_stop_lease_websocket
from .load_execution_stop_recovery import async_initialize_stop_recovery
from .load_execution_stop_resolution_ws_api import async_register_load_execution_stop_resolution_websocket
from .load_execution_stop_scheduler_ws_api import async_register_load_execution_stop_scheduler_websocket
from .load_plan_ws_api import async_register_load_plan_websocket
from .load_profiles_ws_api import async_register_load_profiles_websocket
from .panel import async_register_panel
from .providers.visionq import VisionQApiClient
from .site_capacity_ws_api import async_register_site_capacity_websocket
from .spot_price_settings_ws_api import async_register_spot_price_settings_websocket
from .spot_price_ws_api import async_register_spot_price_websocket
from .tariff_discovery_ws_api import async_register_tariff_discovery_websocket
from .tariff_update_runtime import (
    async_start_tariff_update_runtime,
    async_stop_tariff_update_runtime,
)
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


async def _async_rollback_failed_setup(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    runtime_registry: EntityDiscoveryRuntimeRegistry | None,
    discovery_registered: bool,
    sensors_forwarded: bool,
) -> None:
    """Best-effort cleanup for setup failure without masking the original error."""
    if sensors_forwarded:
        try:
            await hass.config_entries.async_unload_platforms(entry, ["sensor"])
        except Exception:
            pass

    try:
        await async_stop_execution_runtimes(hass, entry.entry_id)
    except Exception:
        pass

    if discovery_registered and runtime_registry is not None:
        try:
            unload_entity_discovery_runtime(
                entry_id=entry.entry_id,
                runtime_registry=runtime_registry,
            )
        except Exception:
            pass

    domain_data = hass.data.get(DOMAIN)
    if isinstance(domain_data, dict):
        domain_data.pop(entry.entry_id, None)


async def _async_cleanup_unloaded_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    runtime_registry: EntityDiscoveryRuntimeRegistry,
) -> None:
    """Finish local cleanup after Home Assistant platforms are already unloaded."""
    first_error: Exception | None = None

    try:
        await async_stop_tariff_update_runtime(hass, entry.entry_id)
    except Exception as err:
        first_error = err

    try:
        await async_stop_execution_runtimes(hass, entry.entry_id)
    except Exception as err:
        if first_error is None:
            first_error = err

    try:
        unload_entity_discovery_runtime(
            entry_id=entry.entry_id,
            runtime_registry=runtime_registry,
        )
    except Exception as err:
        if first_error is None:
            first_error = err

    domain_data = hass.data.get(DOMAIN)
    if isinstance(domain_data, dict):
        domain_data.pop(entry.entry_id, None)

    if first_error is not None:
        raise first_error


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    provider = entry.data.get(CONF_PROVIDER, PROVIDER_VISIONQ)
    if provider == PROVIDER_CEZ_HDO:
        coordinator = CezHdoCoordinator(hass, entry)
    else:
        client = VisionQApiClient(async_get_clientsession(hass), entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
        coordinator = FrakonEnergyCoordinator(hass, entry, client)
        await coordinator.async_initialize_history()
    await coordinator.async_config_entry_first_refresh()

    runtime_registry: EntityDiscoveryRuntimeRegistry | None = None
    discovery_registered = False
    sensors_forwarded = False

    try:
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
        runtime_registry = _runtime_registry(hass)
        setup_entity_discovery_runtime(entry_id=entry.entry_id, runtime_registry=runtime_registry, profile_provider=lambda: technology_profile_from_options(entry.options), registry_provider=lambda: _entity_registry_snapshot(hass), options_provider=lambda: entry.options, options_updater=lambda options: _update_entry_options(hass, entry, options))
        discovery_registered = True
        await async_initialize_lifecycle_recovery(hass, entry_id=entry.entry_id)
        await async_initialize_stop_recovery(hass, entry_id=entry.entry_id)
        async_register_entity_discovery_websocket(hass, runtime_registry)
        async_register_technology_profile_websocket(hass)
        async_register_energy_flow_websocket(hass)
        async_register_site_capacity_websocket(hass)
        async_register_spot_price_websocket(hass)
        async_register_spot_price_settings_websocket(hass)
        async_register_tariff_discovery_websocket(hass)
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
        async_register_load_execution_start_scheduler_websocket(hass)
        async_register_load_execution_pending_run_websocket(hass)
        async_register_load_execution_pending_run_scheduler_websocket(hass)
        async_register_load_execution_arm_websocket(hass)
        async_register_load_execution_safety_status_websocket(hass)
        async_register_load_execution_commissioning_preflight_websocket(hass)
        await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
        sensors_forwarded = True
        await async_register_panel(hass)
        # Start execution workers only after every other setup step has succeeded. The
        # transactional helper rolls back partial worker startup on its own failure.
        await async_start_execution_runtimes(hass, entry.entry_id)
        entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
        # Tariff checks start last. Their immediate probe is background-only and the
        # runtime owns its config-entry unload callback plus partial-start rollback.
        await async_start_tariff_update_runtime(hass, entry)
        return True
    except Exception:
        await _async_rollback_failed_setup(
            hass,
            entry,
            runtime_registry=runtime_registry,
            discovery_registered=discovery_registered,
            sensors_forwarded=sensors_forwarded,
        )
        raise


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    if unloaded:
        await _async_cleanup_unloaded_entry(
            hass,
            entry,
            runtime_registry=_runtime_registry(hass),
        )
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
