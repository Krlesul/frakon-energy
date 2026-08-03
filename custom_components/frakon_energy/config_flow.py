from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries

from .const import DOMAIN, PROVIDER_VISIONQ


class FrakonEnergyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure FRAKON Energy."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            provider = user_input["provider"]
            if provider == PROVIDER_VISIONQ:
                return await self.async_step_visionq()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required("provider", default=PROVIDER_VISIONQ): vol.In({PROVIDER_VISIONQ: "VisionQ"})}
            ),
        )

    async def async_step_visionq(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Collect VisionQ credentials once the official authentication path is confirmed."""
        if user_input is not None:
            await self.async_set_unique_id(f"visionq:{user_input['account_email'].lower()}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"FRAKON Energy – {user_input['account_email']}",
                data={
                    "provider": PROVIDER_VISIONQ,
                    "account_email": user_input["account_email"],
                },
            )

        return self.async_show_form(
            step_id="visionq",
            data_schema=vol.Schema({vol.Required("account_email"): str}),
            description_placeholders={
                "note": "Přihlášení bude doplněno po potvrzení oficiálního VisionQ API."
            },
        )
