from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_ENTRY_CACHE_SUFFIX = "_by_entry"
_LOCK_REGISTRY_SUFFIX = "_locks_by_entry"


def purge_entry_scoped_domain_caches(hass: HomeAssistant, entry_id: str) -> tuple[str, ...]:
    """Drop reload-safe in-process entry cache wrappers after unload/reload.

    Durable state remains in Home Assistant storage. Only domain mappings whose
    names explicitly follow the ``*_by_entry`` cache convention are candidates.
    Execution lock registries (``*_locks_by_entry``) are deliberately preserved:
    an in-flight request may still hold one of those locks while unload/reload is
    completing, and replacing it with a fresh lock would break mutual exclusion.
    Registration markers, panel state, the discovery registry object and unrelated
    domain data are also intentionally left alone.
    """
    if not entry_id:
        raise ValueError("entry_id is required")

    hass_data = getattr(hass, "data", None)
    if not isinstance(hass_data, dict):
        return ()
    domain_data = hass_data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return ()

    purged: list[str] = []
    for key, value in tuple(domain_data.items()):
        if not isinstance(key, str) or not key.endswith(_ENTRY_CACHE_SUFFIX):
            continue
        if key.endswith(_LOCK_REGISTRY_SUFFIX):
            continue
        if not isinstance(value, dict) or entry_id not in value:
            continue
        value.pop(entry_id, None)
        purged.append(key)

    return tuple(sorted(purged))
