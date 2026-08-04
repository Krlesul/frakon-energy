from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .billing import default_settlement_date
from .const import CONF_EUI, DOMAIN, PROVIDER_VISIONQ
from .providers.visionq import VisionQApiClient, VisionQAuthError, VisionQConnectionError

CONF_BILLING_ENABLED = "billing_enabled"
CONF_BILLING_BASELINE_DATE = "billing_baseline_date"
CONF_BILLING_BASELINE_VT = "billing_baseline_vt_kwh"
CONF_BILLING_BASELINE_NT = "billing_baseline_nt_kwh"
CONF_BILLING_CYCLE_START = "billing_cycle_start"
CONF_BILLING_SETTLEMENT_DATE = "billing_settlement_date"
CONF_MONTHLY_ADVANCE = "monthly_advance_czk"
CONF_ADVANCE_VALID_FROM = "advance_valid_from"
CONF_ADVANCE_VALID_TO = "advance_valid_to"


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
            data_schema=vol.Schema(
                {
                    vol.Required("provider", default=PROVIDER_VISIONQ): vol.In(
                        {PROVIDER_VISIONQ: "VisionQ ElIoT"}
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
                    "provider": PROVIDER_VISIONQ,
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

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return FrakonEnergyOptionsFlow(config_entry)


class FrakonEnergyOptionsFlow(config_entries.OptionsFlow):
    """Configure billing-cycle baseline and monthly advances."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        return await self.async_step_billing(user_input)

    async def async_step_billing(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        defaults = self._defaults()

        if user_input is not None:
            try:
                self._validate_billing(user_input)
            except ValueError as err:
                errors["base"] = str(err)
            else:
                return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_BILLING_ENABLED,
                    default=defaults[CONF_BILLING_ENABLED],
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_BILLING_BASELINE_DATE,
                    default=defaults[CONF_BILLING_BASELINE_DATE],
                ): selector.DateSelector(),
                vol.Required(
                    CONF_BILLING_BASELINE_VT,
                    default=defaults[CONF_BILLING_BASELINE_VT],
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        step="any",
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="kWh",
                    )
                ),
                vol.Required(
                    CONF_BILLING_BASELINE_NT,
                    default=defaults[CONF_BILLING_BASELINE_NT],
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        step="any",
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="kWh",
                    )
                ),
                vol.Required(
                    CONF_BILLING_CYCLE_START,
                    default=defaults[CONF_BILLING_CYCLE_START],
                ): selector.DateSelector(),
                vol.Required(
                    CONF_BILLING_SETTLEMENT_DATE,
                    default=defaults[CONF_BILLING_SETTLEMENT_DATE],
                ): selector.DateSelector(),
                vol.Required(
                    CONF_MONTHLY_ADVANCE,
                    default=defaults[CONF_MONTHLY_ADVANCE],
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="Kč",
                    )
                ),
                vol.Required(
                    CONF_ADVANCE_VALID_FROM,
                    default=defaults[CONF_ADVANCE_VALID_FROM],
                ): selector.DateSelector(),
                vol.Optional(
                    CONF_ADVANCE_VALID_TO,
                    default=defaults[CONF_ADVANCE_VALID_TO],
                ): selector.DateSelector(),
            }
        )
        return self.async_show_form(
            step_id="billing",
            data_schema=schema,
            errors=errors,
        )

    def _defaults(self) -> dict[str, Any]:
        today = date.today()
        settlement = default_settlement_date(today)
        baseline = date(settlement.year - 1, 1, 31)
        cycle_start = baseline + timedelta(days=1)
        options = self._entry.options
        return {
            CONF_BILLING_ENABLED: options.get(CONF_BILLING_ENABLED, True),
            CONF_BILLING_BASELINE_DATE: options.get(
                CONF_BILLING_BASELINE_DATE, baseline.isoformat()
            ),
            CONF_BILLING_BASELINE_VT: options.get(CONF_BILLING_BASELINE_VT, 0.0),
            CONF_BILLING_BASELINE_NT: options.get(CONF_BILLING_BASELINE_NT, 0.0),
            CONF_BILLING_CYCLE_START: options.get(
                CONF_BILLING_CYCLE_START, cycle_start.isoformat()
            ),
            CONF_BILLING_SETTLEMENT_DATE: options.get(
                CONF_BILLING_SETTLEMENT_DATE, settlement.isoformat()
            ),
            CONF_MONTHLY_ADVANCE: options.get(CONF_MONTHLY_ADVANCE, 5000.0),
            CONF_ADVANCE_VALID_FROM: options.get(
                CONF_ADVANCE_VALID_FROM, cycle_start.isoformat()
            ),
            CONF_ADVANCE_VALID_TO: options.get(CONF_ADVANCE_VALID_TO, ""),
        }

    @staticmethod
    def _validate_billing(user_input: dict[str, Any]) -> None:
        baseline = date.fromisoformat(user_input[CONF_BILLING_BASELINE_DATE])
        cycle_start = date.fromisoformat(user_input[CONF_BILLING_CYCLE_START])
        settlement = date.fromisoformat(user_input[CONF_BILLING_SETTLEMENT_DATE])
        advance_from = date.fromisoformat(user_input[CONF_ADVANCE_VALID_FROM])
        advance_to_raw = user_input.get(CONF_ADVANCE_VALID_TO)
        advance_to = date.fromisoformat(advance_to_raw) if advance_to_raw else None

        if baseline > cycle_start:
            raise ValueError("baseline_after_cycle_start")
        if settlement < cycle_start:
            raise ValueError("settlement_before_cycle_start")
        if advance_from < cycle_start or advance_from > settlement:
            raise ValueError("advance_outside_cycle")
        if advance_to is not None and advance_to < advance_from:
            raise ValueError("advance_end_before_start")
