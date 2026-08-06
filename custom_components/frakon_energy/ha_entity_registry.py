from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .entity_discovery import EntityDescriptor


@dataclass(frozen=True, slots=True)
class RegistryEntityRecord:
    entity_id: str
    original_name: str | None = None
    name: str | None = None
    device_name: str | None = None
    platform: str | None = None
    domain: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    unit_of_measurement: str | None = None
    disabled: bool = False
    hidden: bool = False
    unavailable: bool = False


def registry_record_from_mapping(value: Mapping[str, Any]) -> RegistryEntityRecord:
    entity_id = str(value.get("entity_id", ""))
    if "." not in entity_id:
        raise ValueError("entity registry record must contain a valid entity_id")
    return RegistryEntityRecord(
        entity_id=entity_id,
        original_name=value.get("original_name"),
        name=value.get("name"),
        device_name=value.get("device_name"),
        platform=value.get("platform"),
        domain=value.get("domain") or entity_id.split(".", 1)[0],
        device_class=value.get("device_class"),
        state_class=value.get("state_class"),
        unit_of_measurement=value.get("unit_of_measurement"),
        disabled=bool(value.get("disabled_by")),
        hidden=bool(value.get("hidden_by")),
        unavailable=bool(value.get("unavailable", False)),
    )


def discovery_descriptors_from_registry(
    records: Iterable[RegistryEntityRecord],
    *,
    include_unavailable: bool = False,
) -> tuple[EntityDescriptor, ...]:
    descriptors: list[EntityDescriptor] = []
    for record in records:
        if record.disabled or record.hidden:
            continue
        if record.unavailable and not include_unavailable:
            continue
        descriptors.append(
            EntityDescriptor(
                entity_id=record.entity_id,
                name=record.name or record.original_name or "",
                device_name=record.device_name or "",
                integration=record.platform or "",
                domain=record.domain or record.entity_id.split(".", 1)[0],
                device_class=record.device_class,
                state_class=record.state_class,
                unit=record.unit_of_measurement,
            )
        )
    return tuple(descriptors)
