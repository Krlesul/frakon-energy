from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_EUI, DOMAIN, PROVIDER_VISIONQ
from .providers.visionq import VisionQApiClient, VisionQAuthError, VisionQConnectionError


class FrakonEnergyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._username: str | None = None
        self._password: str | None = None
        self._devices: list[dict[str, Any]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return await self.async_step_visionq()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("provider", default=PROVIDER_VISIONQ): vol.In({PROVIDER_VISIONQ: "VisionQ ElIoT"})}),
        )

    async def async_step_visionq(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            client = VisionQApiClient(async_get_clientsession(self.hass), self._username, self._password)
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
            data_schema=vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )

    async def async_step_device(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            eui = user_input[CONF_EUI]
            await self.async_set_unique_id(f"visionq:{eui}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"FRAKON Energy – {eui}",
                data={"provider": PROVIDER_VISIONQ, CONF_USERNAME: self._username, CONF_PASSWORD: self._password, CONF_EUI: eui},
            )
        devices = {str(d.get("eui")): str(d.get("description") or d.get("eui")) for d in self._devices if d.get("eui")}
        return self.async_show_form(step_id="device", data_schema=vol.Schema({vol.Required(CONF_EUI): vol.In(devices)}))
