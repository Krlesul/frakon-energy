from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ElectricityPriceList:
    """A VAT-inclusive electricity price list valid from a given date."""

    valid_from: date
    supplier: str
    product: str
    supply_vt_mwh: Decimal
    supply_nt_mwh: Decimal
    distribution_vt_mwh: Decimal
    distribution_nt_mwh: Decimal
    system_services_mwh: Decimal
    electricity_tax_mwh: Decimal
    poze_mwh: Decimal
    supplier_fixed_month: Decimal
    breaker_fixed_month: Decimal
    infrastructure_fixed_month: Decimal

    @property
    def variable_vt_mwh(self) -> Decimal:
        return (
            self.supply_vt_mwh
            + self.distribution_vt_mwh
            + self.system_services_mwh
            + self.electricity_tax_mwh
            + self.poze_mwh
        )

    @property
    def variable_nt_mwh(self) -> Decimal:
        return (
            self.supply_nt_mwh
            + self.distribution_nt_mwh
            + self.system_services_mwh
            + self.electricity_tax_mwh
            + self.poze_mwh
        )

    @property
    def variable_vt_kwh(self) -> Decimal:
        return self.variable_vt_mwh / Decimal("1000")

    @property
    def variable_nt_kwh(self) -> Decimal:
        return self.variable_nt_mwh / Decimal("1000")

    @property
    def fixed_month(self) -> Decimal:
        return (
            self.supplier_fixed_month
            + self.breaker_fixed_month
            + self.infrastructure_fixed_month
        )


def select_price_list(
    price_lists: list[ElectricityPriceList], at_date: date
) -> ElectricityPriceList:
    """Select the newest price list whose validity has already started."""
    candidates = [item for item in price_lists if item.valid_from <= at_date]
    if not candidates:
        raise LookupError(f"No price list is valid on {at_date.isoformat()}")
    return max(candidates, key=lambda item: item.valid_from)
