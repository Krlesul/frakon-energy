from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er


@dataclass(frozen=True, slots=True)
class CezHdoSource:
    """One detected ČEZ HDO source set."""

    source_id: str
    name: str
    schedule_entity_id: str
    low_tariff_entity_id: str | None = None
    current_price_entity_id: str | None = None
    data_valid_entity_id: str | None = None
    signal: str | None = None


async def async_discover_cez_hdo_sources(hass: HomeAssistant) -> list[CezHdoSource]:
    """Discover ČEZ HDO devices without relying on user-specific entity IDs.

    The structured schedule sensor is used as the anchor because it exposes the
    normalized ``schedule`` attribute. Related entities are resolved primarily
    through the entity registry's config-entry/device grouping and secondarily
    through matching signal attributes.
    """

    registry = er.async_get(hass)
    candidates: list[CezHdoSource] = []

    for state in hass.states.async_all("sensor"):
        schedule = state.attributes.get("schedule")
        if not _looks_like_schedule(schedule):
            continue

        entry = registry.async_get(state.entity_id)
        signal = _as_text(state.attributes.get("signal"))
        group = _related_entity_ids(registry, entry)

        low_tariff = _find_related(
            hass,
            group,
            domain="binary_sensor",
            required_tokens=("lowtariffactive", "low_tariff_active"),
            signal=signal,
        )
        current_price = _find_related(
            hass,
            group,
            domain="sensor",
            required_tokens=("currentprice", "current_price"),
            signal=signal,
        )
        data_valid = _find_related(
            hass,
            group,
            domain="binary_sensor",
            required_tokens=("data_valid", "datavalid"),
            signal=signal,
        )

        source_id = (
            entry.config_entry_id
            if entry is not None and entry.config_entry_id
            else signal or state.entity_id
        )
        name = state.attributes.get("friendly_name") or signal or state.entity_id

        candidates.append(
            CezHdoSource(
                source_id=source_id,
                name=str(name),
                schedule_entity_id=state.entity_id,
                low_tariff_entity_id=low_tariff,
                current_price_entity_id=current_price,
                data_valid_entity_id=data_valid,
                signal=signal,
            )
        )

    return _deduplicate(candidates)


def _looks_like_schedule(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    sample = value[0]
    return isinstance(sample, dict) and {"start", "end", "tariff"}.issubset(sample)


def _related_entity_ids(
    registry: er.EntityRegistry, anchor: er.RegistryEntry | None
) -> list[str]:
    if anchor is None:
        return []

    result: list[str] = []
    for entry in registry.entities.values():
        same_config = bool(
            anchor.config_entry_id
            and entry.config_entry_id == anchor.config_entry_id
        )
        same_device = bool(anchor.device_id and entry.device_id == anchor.device_id)
        if same_config or same_device:
            result.append(entry.entity_id)
    return result


def _find_related(
    hass: HomeAssistant,
    entity_ids: list[str],
    *,
    domain: str,
    required_tokens: tuple[str, ...],
    signal: str | None,
) -> str | None:
    for entity_id in entity_ids:
        if not entity_id.startswith(f"{domain}."):
            continue
        lowered = entity_id.lower()
        if any(token in lowered for token in required_tokens):
            return entity_id

    # Fallback for integrations that do not share registry grouping correctly.
    for state in hass.states.async_all(domain):
        lowered = state.entity_id.lower()
        if not any(token in lowered for token in required_tokens):
            continue
        if signal is None or state.attributes.get("signal") == signal:
            return state.entity_id
    return None


def _deduplicate(sources: list[CezHdoSource]) -> list[CezHdoSource]:
    result: dict[str, CezHdoSource] = {}
    for source in sources:
        result[source.source_id] = source
    return sorted(result.values(), key=lambda item: item.name.casefold())


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
