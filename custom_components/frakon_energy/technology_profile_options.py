from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .technology_profile import (
    HouseTechnology,
    HouseTechnologyProfile,
    TechnologySelection,
    default_technology_profile,
)

CONF_TECHNOLOGIES = "technologies"


def technology_profile_from_options(options: Mapping[str, Any]) -> HouseTechnologyProfile:
    """Build a validated technology profile from config-entry options.

    Invalid or unknown records are ignored so an older or partially corrupted option
    payload cannot prevent FRAKON Energy from starting.
    """

    raw_items = options.get(CONF_TECHNOLOGIES)
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
        return default_technology_profile()

    parsed: dict[HouseTechnology, TechnologySelection] = {}
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            continue
        try:
            technology = HouseTechnology(str(raw.get("id", "")))
        except ValueError:
            continue

        raw_entity_ids = raw.get("entity_ids", ())
        if not isinstance(raw_entity_ids, Sequence) or isinstance(raw_entity_ids, (str, bytes)):
            raw_entity_ids = ()
        entity_ids = tuple(
            str(entity_id)
            for entity_id in raw_entity_ids
            if isinstance(entity_id, str) and entity_id and "." in entity_id
        )
        entity_ids = tuple(dict.fromkeys(entity_ids))

        parsed[technology] = TechnologySelection(
            technology=technology,
            enabled=bool(raw.get("enabled", False)),
            entity_ids=entity_ids,
        )

    return HouseTechnologyProfile(
        technologies=tuple(
            parsed.get(
                technology,
                TechnologySelection(technology=technology, enabled=False),
            )
            for technology in HouseTechnology
        )
    )
