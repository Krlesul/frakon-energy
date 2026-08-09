from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_ENTRY_CACHE_SUFFIX = "_by_entry"


def purge_entry_scoped_domain_caches(hass: HomeAssistant, entry_id: str) -> tuple[str, ...]:
    """Drop in-process entry-scoped cache wrappers after unload/reload.

    Durable state remains in Home Assistant storage. Only domain mappings whose
    names explicitly follow the ``*_by_entry`` cache convention are touched.
    Registration markers, panel state, the discovery registry object and unrelated
    domain data are intentionally left alone.
    """
    if not entry_id:
        raise ValueError("entry_id is required")

    domain_data = hass.data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return ()

    purged: list[str] = []
    for key, value in tuple(domain_data.items()):
        if not isinstance(key, str) or not key.endswith(_ENTRY_CACHE_SUFFIX):
            continue
        if not isinstance(value, dict) or entry_id not in value:
            continue
        value.pop(entry_id, None)
        purged.append(key)

    return tuple(sorted(purged))
