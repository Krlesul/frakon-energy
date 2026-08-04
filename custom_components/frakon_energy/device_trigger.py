from __future__ import annotations

from collections.abc import Callable
from typing import Any

import voluptuous as vol

from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_automation import TRIGGER_BASE_SCHEMA
from homeassistant.helpers.typing import ConfigType, TemplateVarsType

from .const import DOMAIN, EVENT_TARIFF_CHANGED

TRIGGER_LOW_TARIFF_STARTED = "low_tariff_started"
TRIGGER_LOW_TARIFF_ENDED = "low_tariff_ended"
TRIGGER_TYPES = {TRIGGER_LOW_TARIFF_STARTED, TRIGGER_LOW_TARIFF_ENDED}

TRIGGER_SCHEMA = TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
    }
)


async def async_get_triggers(
    hass: HomeAssistant,
    device_id: str,
) -> list[dict[str, Any]]:
    """Return HDO tariff-change triggers for a FRAKON Energy HDO device."""

    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return []

    source_id = _source_id_from_device(device)
    if source_id is None:
        return []

    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: trigger_type,
        }
        for trigger_type in (
            TRIGGER_LOW_TARIFF_STARTED,
            TRIGGER_LOW_TARIFF_ENDED,
        )
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: Callable[[TemplateVarsType], Any],
    trigger_info: dict[str, Any],
) -> Callable[[], None]:
    """Attach an HDO device trigger to the FRAKON tariff-change event."""

    device = dr.async_get(hass).async_get(config[CONF_DEVICE_ID])
    source_id = _source_id_from_device(device) if device is not None else None
    if source_id is None:
        raise vol.Invalid("FRAKON Energy HDO device was not found")

    new_tariff = (
        "NT"
        if config[CONF_TYPE] == TRIGGER_LOW_TARIFF_STARTED
        else "VT"
    )
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: EVENT_TARIFF_CHANGED,
            event_trigger.CONF_EVENT_DATA: {
                "source_id": source_id,
                "new_tariff": new_tariff,
            },
        }
    )
    return await event_trigger.async_attach_trigger(
        hass,
        event_config,
        action,
        trigger_info,
        platform_type="device",
    )


def _source_id_from_device(device: dr.DeviceEntry) -> str | None:
    """Extract the HDO source identifier from a FRAKON Energy device."""

    for domain, identifier in device.identifiers:
        if domain != DOMAIN or not identifier.startswith("cez_hdo:"):
            continue
        source_id = identifier.removeprefix("cez_hdo:")
        return source_id or None
    return None
