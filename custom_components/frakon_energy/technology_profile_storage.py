from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .technology_profile import HouseTechnology, HouseTechnologyProfile, TechnologySelection
from .technology_profile_options import CONF_TECHNOLOGIES, technology_profile_from_options


def technology_profile_to_options(profile: HouseTechnologyProfile) -> list[dict[str, Any]]:
    """Serialize a validated house technology profile into config-entry options."""

    return [
        {
            "id": selection.technology.value,
            "enabled": selection.enabled,
            "entity_ids": list(dict.fromkeys(selection.entity_ids)),
        }
        for selection in profile.technologies
    ]


def update_technology_enabled(
    options: Mapping[str, Any],
    technology: HouseTechnology | str,
    enabled: bool,
) -> dict[str, Any]:
    """Return options with one technology enabled or disabled.

    Unrelated integration settings and existing entity assignments are preserved.
    """

    technology_id = technology if isinstance(technology, HouseTechnology) else HouseTechnology(technology)
    current = technology_profile_from_options(options)
    selections = []
    for selection in current.technologies:
        if selection.technology == technology_id:
            selection = TechnologySelection(
                technology=selection.technology,
                enabled=bool(enabled),
                entity_ids=selection.entity_ids,
            )
        selections.append(selection)

    updated = dict(options)
    updated[CONF_TECHNOLOGIES] = technology_profile_to_options(
        HouseTechnologyProfile(technologies=tuple(selections))
    )
    return updated
