from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .entity_discovery_lifecycle import EntityDiscoveryRuntimeRegistry
from .entity_discovery_runtime import EntityDiscoveryRuntime
from .entity_discovery_runtime_factory import build_entity_discovery_runtime
from .ha_entity_registry import RegistryEntityRecord
from .technology_profile import HouseTechnologyProfile


def setup_entity_discovery_runtime(
    *,
    entry_id: str,
    runtime_registry: EntityDiscoveryRuntimeRegistry,
    profile_provider: Callable[[], HouseTechnologyProfile],
    registry_provider: Callable[[], Iterable[RegistryEntityRecord]],
    options_provider: Callable[[], Mapping[str, Any]],
    options_updater: Callable[[Mapping[str, Any]], None],
) -> EntityDiscoveryRuntime:
    """Build and register an entity-discovery runtime for one config entry."""

    runtime = build_entity_discovery_runtime(
        profile_provider=profile_provider,
        registry_provider=registry_provider,
        options_provider=options_provider,
        options_updater=options_updater,
    )
    runtime_registry.register(entry_id, runtime)
    return runtime


def unload_entity_discovery_runtime(
    *,
    entry_id: str,
    runtime_registry: EntityDiscoveryRuntimeRegistry,
) -> bool:
    """Remove the runtime for a config entry during unload or reload."""

    return runtime_registry.remove(entry_id) is not None
