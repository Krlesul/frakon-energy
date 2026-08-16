from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .billing import AdvancePeriod, BillingCalculator, BillingCycle, MeterBaseline
from .billing_tariff_selection import has_new_tariff_catalog, select_billing_tariff_prices
from .config_flow import (
    CONF_ADVANCE_VALID_FROM,
    CONF_ADVANCE_VALID_TO,
    CONF_BILLING_BASELINE_DATE,
    CONF_BILLING_BASELINE_NT,
    CONF_BILLING_BASELINE_VT,
    CONF_BILLING_CYCLE_START,
    CONF_BILLING_ENABLED,
    CONF_BILLING_SETTLEMENT_DATE,
    CONF_FIXED_MONTHLY,
    CONF_METER_REPLACED,
    CONF_METER_REPLACEMENT_DATE,
    CONF_MONTHLY_ADVANCE,
    CONF_NEW_METER_START_NT,
    CONF_NEW_METER_START_VT,
    CONF_OLD_METER_END_NT,
    CONF_OLD_METER_END_VT,
    CONF_PRICE_NT,
    CONF_PRICE_VT,
)
from .const import CONF_PROVIDER, DOMAIN, PROVIDER_CEZ_HDO
from .coordinator import FrakonEnergyCoordinator
from .cost import TariffPrices, calculate_cost_projection
from .daily_all_in_costs import price_confirmed_daily_consumption
from .energy_flow_sensor import build_energy_flow_sensors
from .hdo_coordinator import CezHdoCoordinator
from .metering import MeterSegment, total_cycle_consumption
from .providers.cez_hdo import CezHdoSnapshot
from .providers.visionq import VisionQMeasurement


@dataclass(frozen=True, kw_only=True)
class FrakonEnergySensorDescription(SensorEntityDescription):
    value_fn: Callable[[VisionQMeasurement], object]


VISIONQ_SENSORS = (
    FrakonEnergySensorDescription(key="high_rate", name="VT celkem", device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, value_fn=lambda d: d.high_rate_kwh),
    FrakonEnergySensorDescription(key="low_rate", name="NT celkem", device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, value_fn=lambda d: d.low_rate_kwh),
    FrakonEnergySensorDescription(key="total", name="Elektroměr celkem", device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, value_fn=lambda d: d.total_kwh),
    FrakonEnergySensorDescription(key="last_activity", name="Poslední aktivita", device_class=SensorDeviceClass.TIMESTAMP, value_fn=lambda d: datetime.fromtimestamp(d.timestamp).astimezone() if d.timestamp else None),
    FrakonEnergySensorDescription(key="battery_state", name="Stav baterie", device_class=SensorDeviceClass.BATTERY, state_class=SensorStateClass.MEASUREMENT, native_unit_of_measurement=PERCENTAGE, value_fn=lambda d: d.battery_state),
)

BILLING_KEYS = (
    "monthly_advance", "paid_advances", "projected_advances", "baseline_vt", "baseline_nt",
    "cycle_start", "settlement_date", "cycle_consumption_vt", "cycle_consumption_nt",
    "today_consumption", "today_cost", "month_consumption", "accrued_cost", "current_balance",
    "projected_cost", "projected_balance", "recommended_advance",
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if entry.data.get(CONF_PROVIDER) == PROVIDER_CEZ_HDO:
        async_add_entities(CezHdoSensor(coordinator, key, name) for key, name in (
            ("tariff", "HDO tarif"), ("interval", "HDO aktivní interval"), ("next_switch", "HDO další přepnutí"),
            ("countdown", "HDO odpočet"), ("today_schedule", "HDO dnešní rozvrh"), ("data_valid", "HDO data platná"),
            ("current_price", "HDO aktuální cena"),
        ))
        return
    entities: list[SensorEntity] = [FrakonEnergySensor(coordinator, description) for description in VISIONQ_SENSORS]
    if entry.options.get(CONF_BILLING_ENABLED, False):
        entities.extend(FrakonBillingSensor(coordinator, entry, key) for key in BILLING_KEYS)
    entities.extend(build_energy_flow_sensors(hass, entry))
    async_add_entities(entities)


class FrakonEnergySensor(CoordinatorEntity[FrakonEnergyCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: FrakonEnergyCoordinator, description: FrakonEnergySensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"visionq_{coordinator.eui}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"visionq:{coordinator.eui}")},
            "name": f"FRAKON Energy – VisionQ {coordinator.eui}",
            "manufacturer": "VisionQ",
            "model": "ElIoT Energy Monitor",
        }

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.coordinator.data)


class FrakonBillingSensor(CoordinatorEntity[FrakonEnergyCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: FrakonEnergyCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._key = key
        self._attr_unique_id = f"visionq_{coordinator.eui}_billing_{key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"visionq:{coordinator.eui}")},
            "name": f"FRAKON Energy – VisionQ {coordinator.eui}",
            "manufacturer": "FRAKON",
            "model": "Energy billing",
        }
        self._configure_description()

    def _configure_description(self) -> None:
        names = {
            "monthly_advance": "Měsíční záloha", "paid_advances": "Zaplacené zálohy", "projected_advances": "Zálohy za celé období",
            "baseline_vt": "Počáteční stav VT", "baseline_nt": "Počáteční stav NT", "cycle_start": "Začátek zúčtovacího období",
            "settlement_date": "Předpokládané vyúčtování", "cycle_consumption_vt": "Spotřeba období VT", "cycle_consumption_nt": "Spotřeba období NT",
            "today_consumption": "Spotřeba dnes celkem", "today_cost": "Náklady dnes", "month_consumption": "Spotřeba tento měsíc", "accrued_cost": "Dosavadní náklady",
            "current_balance": "Průběžný přeplatek nebo nedoplatek", "projected_cost": "Odhad celkových nákladů",
            "projected_balance": "Odhad přeplatku nebo nedoplatku", "recommended_advance": "Doporučená záloha",
        }
        self._attr_name = names[self._key]
        monetary = {"monthly_advance", "paid_advances", "projected_advances", "today_cost", "accrued_cost", "current_balance", "projected_cost", "projected_balance", "recommended_advance"}
        energy = {"baseline_vt", "baseline_nt", "cycle_consumption_vt", "cycle_consumption_nt", "today_consumption", "month_consumption"}
        if self._key in monetary:
            self._attr_device_class = SensorDeviceClass.MONETARY
            self._attr_native_unit_of_measurement = "CZK"
            self._attr_state_class = SensorStateClass.TOTAL
        elif self._key in energy:
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
            self._attr_state_class = SensorStateClass.TOTAL
        else:
            self._attr_device_class = SensorDeviceClass.DATE

    @property
    def native_value(self):
        return self._values().get(self._key)

    def _legacy_tariff_prices(self) -> TariffPrices:
        options = self._entry.options
        return TariffPrices(
            high_rate_czk_per_kwh=Decimal(str(options[CONF_PRICE_VT])),
            low_rate_czk_per_kwh=Decimal(str(options[CONF_PRICE_NT])),
            fixed_monthly_czk=Decimal(str(options[CONF_FIXED_MONTHLY])),
        )

    def _tariff_selection(self, day: date):
        options = self._entry.options
        legacy_prices = None
        if not has_new_tariff_catalog(options):
            legacy_prices = self._legacy_tariff_prices()
        return select_billing_tariff_prices(
            options,
            day=day,
            legacy_prices=legacy_prices,
        )

    def _pricing_day(self) -> date:
        today = date.today()
        try:
            cycle_start = date.fromisoformat(self._entry.options[CONF_BILLING_CYCLE_START])
            settlement = date.fromisoformat(self._entry.options[CONF_BILLING_SETTLEMENT_DATE])
        except (KeyError, TypeError, ValueError):
            return today
        return min(max(today, cycle_start), settlement)

    @property
    def extra_state_attributes(self):
        options = self._entry.options
        attributes = {
            "baseline_date": options.get(CONF_BILLING_BASELINE_DATE),
            "advance_valid_from": options.get(CONF_ADVANCE_VALID_FROM),
            "advance_valid_to": options.get(CONF_ADVANCE_VALID_TO) or None,
            "meter_replaced_during_cycle": options.get(CONF_METER_REPLACED, False),
            "meter_replacement_date": options.get(CONF_METER_REPLACEMENT_DATE) or None,
            "old_meter_end_vt_kwh": options.get(CONF_OLD_METER_END_VT),
            "old_meter_end_nt_kwh": options.get(CONF_OLD_METER_END_NT),
            "new_meter_start_vt_kwh": options.get(CONF_NEW_METER_START_VT),
            "new_meter_start_nt_kwh": options.get(CONF_NEW_METER_START_NT),
        }
        try:
            selection = self._tariff_selection(self._pricing_day())
        except (KeyError, LookupError, TypeError, ValueError, AttributeError):
            attributes.update(
                {
                    "price_source": "unavailable",
                    "price_vt_czk_kwh": None,
                    "price_nt_czk_kwh": None,
                    "fixed_monthly_czk": None,
                    "all_in_tariff_fingerprint": None,
                    "tariff_authority_method": None,
                    "tariff_supplier": None,
                    "tariff_product_name": None,
                }
            )
            return attributes

        attributes.update(
            {
                "price_source": selection.source,
                "price_vt_czk_kwh": float(selection.prices.high_rate_czk_per_kwh),
                "price_nt_czk_kwh": float(selection.prices.low_rate_czk_per_kwh),
                "fixed_monthly_czk": float(selection.prices.fixed_monthly_czk),
                "all_in_tariff_fingerprint": selection.all_in_tariff_fingerprint,
                "tariff_authority_method": (
                    selection.authority_method.value
                    if selection.authority_method is not None
                    else None
                ),
                "tariff_supplier": selection.supplier,
                "tariff_product_name": selection.product_name,
            }
        )
        return attributes

    def _meter_segments(self, cycle: BillingCycle) -> tuple[MeterSegment, ...]:
        options = self._entry.options
        baseline_vt = cycle.baseline.high_rate_kwh
        baseline_nt = cycle.baseline.low_rate_kwh
        if not options.get(CONF_METER_REPLACED, False):
            return (MeterSegment(valid_from=cycle.start_date, start_high_rate_kwh=baseline_vt, start_low_rate_kwh=baseline_nt, label="Elektroměr 1"),)
        replacement = date.fromisoformat(options[CONF_METER_REPLACEMENT_DATE])
        return (
            MeterSegment(
                valid_from=cycle.start_date,
                valid_to=replacement,
                start_high_rate_kwh=baseline_vt,
                start_low_rate_kwh=baseline_nt,
                end_high_rate_kwh=Decimal(str(options[CONF_OLD_METER_END_VT])),
                end_low_rate_kwh=Decimal(str(options[CONF_OLD_METER_END_NT])),
                label="Původní elektroměr",
            ),
            MeterSegment(
                valid_from=replacement,
                start_high_rate_kwh=Decimal(str(options[CONF_NEW_METER_START_VT])),
                start_low_rate_kwh=Decimal(str(options[CONF_NEW_METER_START_NT])),
                label="Nový elektroměr",
            ),
        )

    def _values(self) -> dict[str, object]:
        """Return independent billing facts even when tariff pricing is unavailable."""
        options = self._entry.options
        try:
            cycle = BillingCycle(
                start_date=date.fromisoformat(options[CONF_BILLING_CYCLE_START]),
                expected_settlement_date=date.fromisoformat(options[CONF_BILLING_SETTLEMENT_DATE]),
                baseline=MeterBaseline(
                    reading_date=date.fromisoformat(options[CONF_BILLING_BASELINE_DATE]),
                    high_rate_kwh=Decimal(str(options[CONF_BILLING_BASELINE_VT])),
                    low_rate_kwh=Decimal(str(options[CONF_BILLING_BASELINE_NT])),
                ),
            )
            advance_to_raw = options.get(CONF_ADVANCE_VALID_TO)
            advance = AdvancePeriod(
                valid_from=date.fromisoformat(options[CONF_ADVANCE_VALID_FROM]),
                valid_to=date.fromisoformat(advance_to_raw) if advance_to_raw else None,
                monthly_amount_czk=Decimal(str(options[CONF_MONTHLY_ADVANCE])),
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            return {}

        today = date.today()
        as_of = min(max(today, cycle.start_date), cycle.expected_settlement_date)
        values: dict[str, object] = {
            "monthly_advance": advance.monthly_amount_czk,
            "paid_advances": BillingCalculator.sum_advances(
                cycle.start_date, as_of, (advance,)
            ),
            "projected_advances": BillingCalculator.sum_advances(
                cycle.start_date, cycle.expected_settlement_date, (advance,)
            ),
            "baseline_vt": cycle.baseline.high_rate_kwh,
            "baseline_nt": cycle.baseline.low_rate_kwh,
            "cycle_start": cycle.start_date,
            "settlement_date": cycle.expected_settlement_date,
            "cycle_consumption_vt": None,
            "cycle_consumption_nt": None,
            "today_consumption": None,
            "today_cost": None,
            "month_consumption": None,
            "accrued_cost": None,
            "current_balance": None,
            "projected_cost": None,
            "projected_balance": None,
            "recommended_advance": None,
        }

        consumption_vt: Decimal | None = None
        consumption_nt: Decimal | None = None
        try:
            measurement = self.coordinator.data
            consumption_vt, consumption_nt = total_cycle_consumption(
                self._meter_segments(cycle),
                cycle_start=cycle.start_date,
                settlement_date=cycle.expected_settlement_date,
                current_high_rate_kwh=Decimal(str(measurement.high_rate_kwh)),
                current_low_rate_kwh=Decimal(str(measurement.low_rate_kwh)),
            )
            values["cycle_consumption_vt"] = consumption_vt
            values["cycle_consumption_nt"] = consumption_nt
        except (KeyError, TypeError, ValueError, AttributeError):
            pass

        try:
            daily = tuple(self.coordinator.history.daily_consumption())
            today_items = tuple(item for item in daily if item.day == today)
            month_items = tuple(
                item
                for item in daily
                if item.day.year == today.year and item.day.month == today.month
            )
            if today_items:
                values["today_consumption"] = sum(
                    (item.high_rate_kwh + item.low_rate_kwh for item in today_items),
                    Decimal("0"),
                )
                try:
                    priced_today = price_confirmed_daily_consumption(options, today_items)
                    if priced_today:
                        values["today_cost"] = sum(
                            (item.variable_cost_czk for item in priced_today),
                            Decimal("0"),
                        )
                except (LookupError, TypeError, ValueError, AttributeError):
                    pass
            if month_items:
                values["month_consumption"] = sum(
                    (item.high_rate_kwh + item.low_rate_kwh for item in month_items),
                    Decimal("0"),
                )
        except (TypeError, ValueError, AttributeError):
            pass

        if consumption_vt is None or consumption_nt is None:
            return values

        try:
            tariff_selection = self._tariff_selection(as_of)
            cost = calculate_cost_projection(
                cycle_start=cycle.start_date,
                settlement_date=cycle.expected_settlement_date,
                as_of=as_of,
                baseline_high_rate_kwh=Decimal("0"),
                baseline_low_rate_kwh=Decimal("0"),
                current_high_rate_kwh=consumption_vt,
                current_low_rate_kwh=consumption_nt,
                prices=tariff_selection.prices,
            )
            billing = BillingCalculator.calculate(
                cycle=cycle,
                as_of=as_of,
                advances=(advance,),
                accrued_cost_czk=cost.accrued_total_cost_czk,
                projected_total_cost_czk=cost.projected_total_cost_czk,
            )
        except (KeyError, LookupError, TypeError, ValueError, AttributeError):
            return values

        values.update(
            {
                "paid_advances": billing.paid_advances_czk,
                "projected_advances": billing.projected_total_advances_czk,
                "accrued_cost": billing.accrued_cost_czk,
                "current_balance": billing.current_balance_czk,
                "projected_cost": billing.projected_total_cost_czk,
                "projected_balance": billing.projected_settlement_balance_czk,
                "recommended_advance": billing.recommended_monthly_advance_czk,
            }
        )
        return values


class CezHdoSensor(CoordinatorEntity[CezHdoCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: CezHdoCoordinator, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"cez_hdo_{coordinator.source.source_id}_{key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"cez_hdo:{coordinator.source.source_id}")},
            "name": "FRAKON Energy – ČEZ HDO",
            "manufacturer": "FRAKON Energy",
            "model": "ČEZ HDO adapter",
        }
        if key == "next_switch":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif key == "current_price":
            self._attr_device_class = SensorDeviceClass.MONETARY
            self._attr_native_unit_of_measurement = "Kč/kWh"

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.source_available

    @property
    def native_value(self):
        data: CezHdoSnapshot = self.coordinator.data
        if self._key == "tariff": return data.tariff
        if self._key == "interval": return None if data.interval_start is None or data.interval_end is None else f"{data.tariff} {data.interval_start:%H:%M}–{data.interval_end:%H:%M}"
        if self._key == "next_switch": return data.next_switch
        if self._key == "countdown":
            if data.countdown_seconds is None: return None
            hours, remainder = divmod(data.countdown_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        if self._key == "today_schedule":
            return " | ".join(f"{datetime.fromisoformat(item['start']):%H:%M}–{datetime.fromisoformat(item['end']):%H:%M} {item['tariff']}" for item in data.today_schedule)
        if self._key == "data_valid": return data.data_valid
        if self._key == "current_price": return data.current_price
        return None

    @property
    def extra_state_attributes(self):
        return {"schedule": list(self.coordinator.data.today_schedule)} if self._key == "today_schedule" else None
