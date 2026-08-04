from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_EUI,
    CONF_HDO_CURRENT_PRICE_ENTITY,
    CONF_HDO_DATA_VALID_ENTITY,
    CONF_HDO_LOW_TARIFF_ENTITY,
    CONF_HDO_SCHEDULE_ENTITY,
    CONF_HDO_SOURCE_ID,
    CONF_PROVIDER,
    DOMAIN,
    PROVIDER_CEZ_HDO,
    PROVIDER_VISIONQ,
)
from .providers.cez_hdo_discovery import CezHdoSource, async_discover_cez_hdo_sources
from .providers.visionq import VisionQApiClient, VisionQAuthError, VisionQConnectionError


class FrakonEnergyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._username: str | None = None
        self._password: str | None = None
        self._devices: list[dict[str, Any]] = []
        self._hdo_sources: list[CezHdoSource] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            provider = user_input[CONF_PROVIDER]
            if provider == PROVIDER_CEZ_HDO:
                return await self.async_step_cez_hdo()
            return await self.async_step_visionq()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PROVIDER, default=PROVIDER_VISIONQ): vol.In(
                        {
                            PROVIDER_VISIONQ: "VisionQ ElIoT",
                            PROVIDER_CEZ_HDO: "ČEZ HDO",
                        }
                    )
                }
            ),
        )

    async def async_step_visionq(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            client = VisionQApiClient(
                async_get_clientsession(self.hass), self._username, self._password
            )
            try:
                self._devices = await client.async_get_devices()
            except VisionQAuthError:
                errors["base"] = "invalid_auth"
            except VisionQConnectionError:
                errors["base"] = "cannot_connect"
            else:
                if not self._devices:
                    errors["base"] = "no_devices_found"
                else:
                    return await self.async_step_device()

        return self.async_show_form(
            step_id="visionq",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_device(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            eui = user_input[CONF_EUI]
            await self.async_set_unique_id(f"visionq:{eui}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"FRAKON Energy – {eui}",
                data={
                    CONF_PROVIDER: PROVIDER_VISIONQ,
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    CONF_EUI: eui,
                },
            )

        devices = {
            str(d.get("eui")): str(d.get("description") or d.get("eui"))
            for d in self._devices
            if d.get("eui")
        }
        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema({vol.Required(CONF_EUI): vol.In(devices)}),
        )

    async def async_step_cez_hdo(self, user_input: dict[str, Any] | None = None):
        if not self._hdo_sources:
            self._hdo_sources = await async_discover_cez_hdo_sources(self.hass)

        if not self._hdo_sources:
            return self.async_abort(reason="cez_hdo_not_found")

        if user_input is None and len(self._hdo_sources) == 1:
            return await self._create_hdo_entry(self._hdo_sources[0])

        if user_input is not None:
            source_id = user_input[CONF_HDO_SOURCE_ID]
            source = next(item for item in self._hdo_sources if item.source_id == source_id)
            return await self._create_hdo_entry(source)

        choices = {item.source_id: item.name for item in self._hdo_sources}
        return self.async_show_form(
            step_id="cez_hdo",
            data_schema=vol.Schema(
                {vol.Required(CONF_HDO_SOURCE_ID): vol.In(choices)}
            ),
        )

    async def _create_hdo_entry(self, source: CezHdoSource):
        await self.async_set_unique_id(f"cez_hdo:{source.source_id}")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"FRAKON Energy – ČEZ HDO {source.signal or source.source_id}",
            data={
                CONF_PROVIDER: PROVIDER_CEZ_HDO,
                CONF_HDO_SOURCE_ID: source.source_id,
                CONF_HDO_SCHEDULE_ENTITY: source.schedule_entity_id,
                CONF_HDO_LOW_TARIFF_ENTITY: source.low_tariff_entity_id,
                CONF_HDO_CURRENT_PRICE_ENTITY: source.current_price_entity_id,
                CONF_HDO_DATA_VALID_ENTITY: source.data_valid_entity_id,
            },
        )
