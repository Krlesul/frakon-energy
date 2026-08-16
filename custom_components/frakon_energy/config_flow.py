from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .billing import next_default_settlement_date
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
from .contracts import (
    Breaker,
    ContractKind,
    Distributor,
    ElectricityContract,
    Supplier,
    append_electricity_contract,
    confirm_electricity_contract,
    contract_fingerprint,
    contracts_from_options,
)
from .providers.cez_hdo_discovery import CezHdoSource, async_discover_cez_hdo_sources
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
CONF_PRICE_VT = "price_vt_czk_kwh"
CONF_PRICE_NT = "price_nt_czk_kwh"
CONF_FIXED_MONTHLY = "fixed_monthly_czk"
CONF_METER_REPLACED = "meter_replaced_during_cycle"
CONF_METER_REPLACEMENT_DATE = "meter_replacement_date"
CONF_OLD_METER_END_VT = "old_meter_end_vt_kwh"
CONF_OLD_METER_END_NT = "old_meter_end_nt_kwh"
CONF_NEW_METER_START_VT = "new_meter_start_vt_kwh"
CONF_NEW_METER_START_NT = "new_meter_start_nt_kwh"

CONF_CONTRACT_SUPPLIER = "contract_supplier"
CONF_CONTRACT_DISTRIBUTOR = "contract_distributor"
CONF_CONTRACT_PRODUCT = "contract_product"
CONF_CONTRACT_KIND = "contract_kind"
CONF_CONTRACT_DISTRIBUTION_TARIFF = "contract_distribution_tariff"
CONF_CONTRACT_BREAKER_PHASES = "contract_breaker_phases"
CONF_CONTRACT_BREAKER_AMPERES = "contract_breaker_amperes"
CONF_CONTRACT_VALID_FROM = "contract_valid_from"
CONF_CONTRACT_VALID_TO = "contract_valid_to"
CONF_CONTRACT_FIXATION_END = "contract_fixation_end"
CONF_CONTRACT_CONFIRM = "contract_confirm"

_SUPPLIER_OPTIONS = {
    Supplier.CEZ.value: "ČEZ",
    Supplier.EON.value: "E.ON",
    Supplier.PRE.value: "PRE",
    Supplier.MND.value: "MND",
    Supplier.INNOGY.value: "innogy",
    Supplier.CENTROPOL.value: "Centropol",
    Supplier.EPET.value: "EPET",
    Supplier.OTHER.value: "Other / Jiný",
}
_DISTRIBUTOR_OPTIONS = {
    Distributor.CEZ_DISTRIBUCE.value: "ČEZ Distribuce",
    Distributor.EG_D.value: "EG.D",
    Distributor.PRE_DISTRIBUCE.value: "PREdistribuce",
}
_CONTRACT_KIND_OPTIONS = {
    ContractKind.FIXED.value: "Fixace / Fixed",
    ContractKind.INDEFINITE.value: "Na dobu neurčitou / Indefinite",
    ContractKind.SPOT.value: "Spot",
}


class FrakonEnergyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._username: str | None = None
        self._password: str | None = None
        self._devices: list[dict[str, Any]] = []
        self._hdo_sources: list[CezHdoSource] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return await (
                self.async_step_cez_hdo()
                if user_input[CONF_PROVIDER] == PROVIDER_CEZ_HDO
                else self.async_step_visionq()
            )
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
                if self._devices:
                    return await self.async_step_device()
                errors["base"] = "no_devices_found"
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
            source = next(
                item
                for item in self._hdo_sources
                if item.source_id == user_input[CONF_HDO_SOURCE_ID]
            )
            return await self._create_hdo_entry(source)
        return self.async_show_form(
            step_id="cez_hdo",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HDO_SOURCE_ID): vol.In(
                        {item.source_id: item.name for item in self._hdo_sources}
                    )
                }
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

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return FrakonEnergyOptionsFlow()


class FrakonEnergyOptionsFlow(config_entries.OptionsFlow):
    def __init__(self) -> None:
        self._pending_contract: ElectricityContract | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if self.config_entry.data.get(CONF_PROVIDER) == PROVIDER_CEZ_HDO:
            return self.async_abort(reason="no_options_available")
        return self.async_show_menu(
            step_id="init",
            menu_options=["billing", "contract"],
        )

    async def async_step_billing(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        defaults = self._billing_defaults()
        if user_input is not None:
            try:
                self._validate_billing(user_input)
            except ValueError as err:
                errors["base"] = str(err)
            else:
                updated = dict(self.config_entry.options)
                updated.update(user_input)
                return self.async_create_entry(title="", data=updated)

        number = lambda unit, step="any": selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                step=step,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement=unit,
            )
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_BILLING_ENABLED, default=defaults[CONF_BILLING_ENABLED]
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_BILLING_BASELINE_DATE,
                    default=defaults[CONF_BILLING_BASELINE_DATE],
                ): selector.DateSelector(),
                vol.Required(
                    CONF_BILLING_BASELINE_VT,
                    default=defaults[CONF_BILLING_BASELINE_VT],
                ): number("kWh"),
                vol.Required(
                    CONF_BILLING_BASELINE_NT,
                    default=defaults[CONF_BILLING_BASELINE_NT],
                ): number("kWh"),
                vol.Required(
                    CONF_BILLING_CYCLE_START,
                    default=defaults[CONF_BILLING_CYCLE_START],
                ): selector.DateSelector(),
                vol.Required(
                    CONF_BILLING_SETTLEMENT_DATE,
                    default=defaults[CONF_BILLING_SETTLEMENT_DATE],
                ): selector.DateSelector(),
                vol.Required(
                    CONF_METER_REPLACED, default=defaults[CONF_METER_REPLACED]
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_METER_REPLACEMENT_DATE,
                    default=defaults[CONF_METER_REPLACEMENT_DATE],
                ): selector.DateSelector(),
                vol.Optional(
                    CONF_OLD_METER_END_VT, default=defaults[CONF_OLD_METER_END_VT]
                ): number("kWh"),
                vol.Optional(
                    CONF_OLD_METER_END_NT, default=defaults[CONF_OLD_METER_END_NT]
                ): number("kWh"),
                vol.Optional(
                    CONF_NEW_METER_START_VT, default=defaults[CONF_NEW_METER_START_VT]
                ): number("kWh"),
                vol.Optional(
                    CONF_NEW_METER_START_NT, default=defaults[CONF_NEW_METER_START_NT]
                ): number("kWh"),
                vol.Required(
                    CONF_MONTHLY_ADVANCE, default=defaults[CONF_MONTHLY_ADVANCE]
                ): number("Kč", 1),
                vol.Required(
                    CONF_ADVANCE_VALID_FROM, default=defaults[CONF_ADVANCE_VALID_FROM]
                ): selector.DateSelector(),
                vol.Optional(
                    CONF_ADVANCE_VALID_TO, default=defaults[CONF_ADVANCE_VALID_TO]
                ): selector.DateSelector(),
                vol.Required(CONF_PRICE_VT, default=defaults[CONF_PRICE_VT]): number(
                    "Kč/kWh"
                ),
                vol.Required(CONF_PRICE_NT, default=defaults[CONF_PRICE_NT]): number(
                    "Kč/kWh"
                ),
                vol.Required(
                    CONF_FIXED_MONTHLY, default=defaults[CONF_FIXED_MONTHLY]
                ): number("Kč/měsíc"),
            }
        )
        return self.async_show_form(
            step_id="billing", data_schema=schema, errors=errors
        )

    async def async_step_contract(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        defaults = self._contract_defaults()
        if user_input is not None:
            try:
                self._pending_contract = self._contract_from_input(user_input)
            except (KeyError, TypeError, ValueError):
                errors["base"] = "invalid_contract"
            else:
                return await self.async_step_contract_confirm()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CONTRACT_SUPPLIER,
                    default=defaults[CONF_CONTRACT_SUPPLIER],
                ): vol.In(_SUPPLIER_OPTIONS),
                vol.Required(
                    CONF_CONTRACT_DISTRIBUTOR,
                    default=defaults[CONF_CONTRACT_DISTRIBUTOR],
                ): vol.In(_DISTRIBUTOR_OPTIONS),
                vol.Required(
                    CONF_CONTRACT_PRODUCT,
                    default=defaults[CONF_CONTRACT_PRODUCT],
                ): selector.TextSelector(),
                vol.Required(
                    CONF_CONTRACT_KIND,
                    default=defaults[CONF_CONTRACT_KIND],
                ): vol.In(_CONTRACT_KIND_OPTIONS),
                vol.Required(
                    CONF_CONTRACT_DISTRIBUTION_TARIFF,
                    default=defaults[CONF_CONTRACT_DISTRIBUTION_TARIFF],
                ): selector.TextSelector(),
                vol.Required(
                    CONF_CONTRACT_BREAKER_PHASES,
                    default=str(defaults[CONF_CONTRACT_BREAKER_PHASES]),
                ): vol.In({"1": "1", "3": "3"}),
                vol.Required(
                    CONF_CONTRACT_BREAKER_AMPERES,
                    default=defaults[CONF_CONTRACT_BREAKER_AMPERES],
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="A",
                    )
                ),
                vol.Required(
                    CONF_CONTRACT_VALID_FROM,
                    default=defaults[CONF_CONTRACT_VALID_FROM],
                ): selector.DateSelector(),
                vol.Optional(
                    CONF_CONTRACT_VALID_TO,
                    default=defaults[CONF_CONTRACT_VALID_TO],
                ): selector.DateSelector(),
                vol.Optional(
                    CONF_CONTRACT_FIXATION_END,
                    default=defaults[CONF_CONTRACT_FIXATION_END],
                ): selector.DateSelector(),
            }
        )
        return self.async_show_form(
            step_id="contract", data_schema=schema, errors=errors
        )

    async def async_step_contract_confirm(
        self, user_input: dict[str, Any] | None = None
    ):
        if self._pending_contract is None:
            return await self.async_step_contract()

        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_CONTRACT_CONFIRM) is not True:
                errors["base"] = "contract_confirmation_required"
            else:
                candidate = self._pending_contract
                fingerprint = contract_fingerprint(candidate)
                updated = append_electricity_contract(
                    self.config_entry.options, candidate
                )
                updated = confirm_electricity_contract(updated, fingerprint)
                return self.async_create_entry(title="", data=updated)

        contract = self._pending_contract
        placeholders = {
            "supplier": _SUPPLIER_OPTIONS[contract.supplier.value],
            "product": contract.product_name,
            "contract_kind": _CONTRACT_KIND_OPTIONS[contract.contract_kind.value],
            "distributor": _DISTRIBUTOR_OPTIONS[contract.distributor.value],
            "tariff": contract.distribution_tariff,
            "breaker": contract.breaker.code,
            "valid_from": contract.valid_from.isoformat(),
            "valid_to": contract.valid_to.isoformat() if contract.valid_to else "—",
            "fixation_end": (
                contract.fixation_end.isoformat() if contract.fixation_end else "—"
            ),
        }
        return self.async_show_form(
            step_id="contract_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONTRACT_CONFIRM, default=False
                    ): selector.BooleanSelector()
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    def _billing_defaults(self) -> dict[str, Any]:
        today = date.today()
        settlement = next_default_settlement_date(today)
        baseline = date(settlement.year - 1, 1, 31)
        cycle_start = baseline + timedelta(days=1)
        options = self.config_entry.options
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
            CONF_METER_REPLACED: options.get(CONF_METER_REPLACED, False),
            CONF_METER_REPLACEMENT_DATE: options.get(
                CONF_METER_REPLACEMENT_DATE, ""
            ),
            CONF_OLD_METER_END_VT: options.get(CONF_OLD_METER_END_VT, 0.0),
            CONF_OLD_METER_END_NT: options.get(CONF_OLD_METER_END_NT, 0.0),
            CONF_NEW_METER_START_VT: options.get(CONF_NEW_METER_START_VT, 0.0),
            CONF_NEW_METER_START_NT: options.get(CONF_NEW_METER_START_NT, 0.0),
            CONF_MONTHLY_ADVANCE: options.get(CONF_MONTHLY_ADVANCE, 5000.0),
            CONF_ADVANCE_VALID_FROM: options.get(
                CONF_ADVANCE_VALID_FROM, cycle_start.isoformat()
            ),
            CONF_ADVANCE_VALID_TO: options.get(CONF_ADVANCE_VALID_TO, ""),
            CONF_PRICE_VT: options.get(CONF_PRICE_VT, 7.52),
            CONF_PRICE_NT: options.get(CONF_PRICE_NT, 4.67),
            CONF_FIXED_MONTHLY: options.get(CONF_FIXED_MONTHLY, 0.0),
        }

    def _contract_defaults(self) -> dict[str, Any]:
        today = date.today()
        stored = contracts_from_options(self.config_entry.options)
        if stored:
            contract = max(stored, key=lambda item: item.valid_from)
            return {
                CONF_CONTRACT_SUPPLIER: contract.supplier.value,
                CONF_CONTRACT_DISTRIBUTOR: contract.distributor.value,
                CONF_CONTRACT_PRODUCT: contract.product_name,
                CONF_CONTRACT_KIND: contract.contract_kind.value,
                CONF_CONTRACT_DISTRIBUTION_TARIFF: contract.distribution_tariff,
                CONF_CONTRACT_BREAKER_PHASES: str(contract.breaker.phases),
                CONF_CONTRACT_BREAKER_AMPERES: contract.breaker.amperes,
                CONF_CONTRACT_VALID_FROM: contract.valid_from.isoformat(),
                CONF_CONTRACT_VALID_TO: (
                    contract.valid_to.isoformat()
                    if contract.valid_to is not None
                    else ""
                ),
                CONF_CONTRACT_FIXATION_END: (
                    contract.fixation_end.isoformat()
                    if contract.fixation_end is not None
                    else ""
                ),
            }
        return {
            CONF_CONTRACT_SUPPLIER: Supplier.CEZ.value,
            CONF_CONTRACT_DISTRIBUTOR: Distributor.CEZ_DISTRIBUCE.value,
            CONF_CONTRACT_PRODUCT: "",
            CONF_CONTRACT_KIND: ContractKind.INDEFINITE.value,
            CONF_CONTRACT_DISTRIBUTION_TARIFF: "D25d",
            CONF_CONTRACT_BREAKER_PHASES: "3",
            CONF_CONTRACT_BREAKER_AMPERES: 25,
            CONF_CONTRACT_VALID_FROM: today.isoformat(),
            CONF_CONTRACT_VALID_TO: "",
            CONF_CONTRACT_FIXATION_END: "",
        }

    @staticmethod
    def _contract_from_input(user_input: dict[str, Any]) -> ElectricityContract:
        amperes_raw = float(user_input[CONF_CONTRACT_BREAKER_AMPERES])
        if not amperes_raw.is_integer():
            raise ValueError("breaker amperage must be an integer")
        valid_to_raw = user_input.get(CONF_CONTRACT_VALID_TO)
        contract_kind = ContractKind(str(user_input[CONF_CONTRACT_KIND]))
        fixation_end_raw = user_input.get(CONF_CONTRACT_FIXATION_END)
        if contract_kind != ContractKind.FIXED:
            fixation_end_raw = None
        return ElectricityContract(
            supplier=Supplier(str(user_input[CONF_CONTRACT_SUPPLIER])),
            distributor=Distributor(str(user_input[CONF_CONTRACT_DISTRIBUTOR])),
            product_name=str(user_input[CONF_CONTRACT_PRODUCT]).strip(),
            contract_kind=contract_kind,
            distribution_tariff=str(
                user_input[CONF_CONTRACT_DISTRIBUTION_TARIFF]
            ).strip(),
            breaker=Breaker(
                int(user_input[CONF_CONTRACT_BREAKER_PHASES]),
                int(amperes_raw),
            ),
            valid_from=date.fromisoformat(user_input[CONF_CONTRACT_VALID_FROM]),
            valid_to=(
                date.fromisoformat(valid_to_raw) if valid_to_raw else None
            ),
            fixation_end=(
                date.fromisoformat(fixation_end_raw)
                if fixation_end_raw
                else None
            ),
            customer_confirmed=False,
        )

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
        if advance_to is not None and advance_to > settlement:
            raise ValueError("advance_end_after_settlement")
        if user_input.get(CONF_METER_REPLACED):
            replacement_raw = user_input.get(CONF_METER_REPLACEMENT_DATE)
            if not replacement_raw:
                raise ValueError("replacement_date_required")
            replacement = date.fromisoformat(replacement_raw)
            if replacement < cycle_start or replacement > settlement:
                raise ValueError("replacement_outside_cycle")
            old_vt = float(user_input.get(CONF_OLD_METER_END_VT, 0))
            old_nt = float(user_input.get(CONF_OLD_METER_END_NT, 0))
            new_vt = float(user_input.get(CONF_NEW_METER_START_VT, 0))
            new_nt = float(user_input.get(CONF_NEW_METER_START_NT, 0))
            if old_vt < float(user_input[CONF_BILLING_BASELINE_VT]):
                raise ValueError("old_meter_vt_below_start")
            if old_nt < float(user_input[CONF_BILLING_BASELINE_NT]):
                raise ValueError("old_meter_nt_below_start")
            if new_vt < 0 or new_nt < 0:
                raise ValueError("new_meter_start_negative")
