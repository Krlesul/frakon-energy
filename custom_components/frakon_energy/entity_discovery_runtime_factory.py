from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .entity_discovery_runtime import EntityDiscoveryRuntime
from .ha_entity_registry import RegistryEntityRecord
from .technology_profile import HouseTechnologyProfile


def build_entity_discovery_runtime(
    *,
    profile_provider: Callable[[], HouseTechnologyProfile],
    registry_provider: Callable[[], Iterable[RegistryEntityRecord]],
    options_provider: Callable[[], Mapping[str, Any]],
    options_updater: Callable[[Mapping[str, Any]], None],
) -> EntityDiscoveryRuntime:
    """Create the runtime used by Home Assistant WebSocket handlers.

    Keeping construction in one factory gives integration setup a single place to bind
    the active config entry, entity registry and technology profile without duplicating
    persistence or discovery logic.
    """

    return EntityDiscoveryRuntime(
        profile_provider=profile_provider,
        registry_provider=registry_provider,
        options_provider=options_provider,
        options_updater=options_updater,
    )
