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
    CONF_MONTHLY_ADVANCE,
    CONF_PRICE_NT,
    CONF_PRICE_VT,
)
from .const import CONF_PROVIDER, DOMAIN, PROVIDER_CEZ_HDO
from .coordinator import FrakonEnergyCoordinator
from .cost import TariffPrices, calculate_cost_projection
from .hdo_coordinator import CezHdoCoordinator
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
    "today_consumption", "month_consumption", "accrued_cost", "current_balance",
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
            "today_consumption": "Spotřeba dnes celkem", "month_consumption": "Spotřeba tento měsíc", "accrued_cost": "Dosavadní náklady",
            "current_balance": "Průběžný přeplatek nebo nedoplatek", "projected_cost": "Odhad celkových nákladů",
            "projected_balance": "Odhad přeplatku nebo nedoplatku", "recommended_advance": "Doporučená záloha",
        }
        self._attr_name = names[self._key]
        monetary = {"monthly_advance", "paid_advances", "projected_advances", "accrued_cost", "current_balance", "projected_cost", "projected_balance", "recommended_advance"}
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
        values = self._values()
        return values.get(self._key)

    @property
    def extra_state_attributes(self):
        return {
            "baseline_date": self._entry.options.get(CONF_BILLING_BASELINE_DATE),
            "advance_valid_from": self._entry.options.get(CONF_ADVANCE_VALID_FROM),
            "advance_valid_to": self._entry.options.get(CONF_ADVANCE_VALID_TO) or None,
            "price_vt_czk_kwh": self._entry.options.get(CONF_PRICE_VT),
            "price_nt_czk_kwh": self._entry.options.get(CONF_PRICE_NT),
            "fixed_monthly_czk": self._entry.options.get(CONF_FIXED_MONTHLY),
        }

    def _values(self) -> dict[str, object]:
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
            today = date.today()
            as_of = min(max(today, cycle.start_date), cycle.expected_settlement_date)
            measurement = self.coordinator.data
            cost = calculate_cost_projection(
                cycle_start=cycle.start_date,
                settlement_date=cycle.expected_settlement_date,
                as_of=as_of,
                baseline_high_rate_kwh=cycle.baseline.high_rate_kwh,
                baseline_low_rate_kwh=cycle.baseline.low_rate_kwh,
                current_high_rate_kwh=Decimal(str(measurement.high_rate_kwh)),
                current_low_rate_kwh=Decimal(str(measurement.low_rate_kwh)),
                prices=TariffPrices(
                    high_rate_czk_per_kwh=Decimal(str(options[CONF_PRICE_VT])),
                    low_rate_czk_per_kwh=Decimal(str(options[CONF_PRICE_NT])),
                    fixed_monthly_czk=Decimal(str(options[CONF_FIXED_MONTHLY])),
                ),
            )
            billing = BillingCalculator.calculate(
                cycle=cycle, as_of=as_of, advances=(advance,),
                accrued_cost_czk=cost.accrued_total_cost_czk,
                projected_total_cost_czk=cost.projected_total_cost_czk,
            )
            daily = self.coordinator.history.daily_consumption()
            today_total = sum((item.high_rate_kwh + item.low_rate_kwh for item in daily if item.day == today), Decimal("0"))
            month_total = sum((item.high_rate_kwh + item.low_rate_kwh for item in daily if item.day.year == today.year and item.day.month == today.month), Decimal("0"))
            return {
                "monthly_advance": advance.monthly_amount_czk,
                "paid_advances": billing.paid_advances_czk,
                "projected_advances": billing.projected_total_advances_czk,
                "baseline_vt": cycle.baseline.high_rate_kwh,
                "baseline_nt": cycle.baseline.low_rate_kwh,
                "cycle_start": cycle.start_date,
                "settlement_date": cycle.expected_settlement_date,
                "cycle_consumption_vt": cost.high_rate_consumption_kwh,
                "cycle_consumption_nt": cost.low_rate_consumption_kwh,
                "today_consumption": today_total,
                "month_consumption": month_total,
                "accrued_cost": billing.accrued_cost_czk,
                "current_balance": billing.current_balance_czk,
                "projected_cost": billing.projected_total_cost_czk,
                "projected_balance": billing.projected_settlement_balance_czk,
                "recommended_advance": billing.recommended_monthly_advance_czk,
            }
        except (KeyError, TypeError, ValueError, AttributeError):
            return {}


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
