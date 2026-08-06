from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .ha_entity_registry import RegistryEntityRecord


def registry_records_from_home_assistant(
    entries: Iterable[Any],
    *,
    states: Mapping[str, Any] | None = None,
    device_names: Mapping[str, str] | None = None,
) -> tuple[RegistryEntityRecord, ...]:
    """Convert Home Assistant entity-registry entries into discovery records.

    The adapter intentionally reads attributes defensively so registry changes or a
    partially unavailable state cannot prevent FRAKON Energy from starting.
    """

    states = states or {}
    device_names = device_names or {}
    records: list[RegistryEntityRecord] = []

    for entry in entries:
        entity_id = str(getattr(entry, "entity_id", ""))
        if "." not in entity_id:
            continue

        state = states.get(entity_id)
        attributes = getattr(state, "attributes", {}) if state is not None else {}
        if not isinstance(attributes, Mapping):
            attributes = {}

        device_id = getattr(entry, "device_id", None)
        device_name = device_names.get(str(device_id), "") if device_id else ""

        records.append(
            RegistryEntityRecord(
                entity_id=entity_id,
                original_name=getattr(entry, "original_name", None),
                name=getattr(entry, "name", None),
                device_name=device_name or None,
                platform=getattr(entry, "platform", None),
                domain=entity_id.split(".", 1)[0],
                device_class=getattr(entry, "device_class", None)
                or attributes.get("device_class"),
                state_class=attributes.get("state_class"),
                unit_of_measurement=attributes.get("unit_of_measurement"),
                disabled=getattr(entry, "disabled_by", None) is not None,
                hidden=getattr(entry, "hidden_by", None) is not None,
                unavailable=state is None or getattr(state, "state", None) in {
                    "unavailable",
                    "unknown",
                },
            )
        )

    return tuple(records)
