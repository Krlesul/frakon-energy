"""Persistent customer spot-price settings for FRAKON Energy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

CONF_SPOT_EUR_CZK = "spot_eur_czk"
CONF_SPOT_SUPPLIER_FEE = "spot_supplier_fee_czk_kwh"
CONF_SPOT_VARIABLE_ADDITIONS = "spot_variable_additions_czk_kwh"
CONF_SPOT_VAT_PERCENT = "spot_vat_percent"


@dataclass(frozen=True, slots=True)
class SpotPriceSettings:
    eur_czk: float = 25.0
    supplier_fee_czk_kwh: float = 0.0
    variable_additions_czk_kwh: float = 0.0
    vat_percent: float = 21.0

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> "SpotPriceSettings":
        return cls(
            eur_czk=float(options.get(CONF_SPOT_EUR_CZK, 25.0)),
            supplier_fee_czk_kwh=float(options.get(CONF_SPOT_SUPPLIER_FEE, 0.0)),
            variable_additions_czk_kwh=float(options.get(CONF_SPOT_VARIABLE_ADDITIONS, 0.0)),
            vat_percent=float(options.get(CONF_SPOT_VAT_PERCENT, 21.0)),
        ).validated()

    def validated(self) -> "SpotPriceSettings":
        if not 10.0 <= self.eur_czk <= 50.0:
            raise ValueError("EUR/CZK must be between 10 and 50")
        if not -10.0 <= self.supplier_fee_czk_kwh <= 20.0:
            raise ValueError("supplier fee must be between -10 and 20 CZK/kWh")
        if not -10.0 <= self.variable_additions_czk_kwh <= 30.0:
            raise ValueError("variable additions must be between -10 and 30 CZK/kWh")
        if not 0.0 <= self.vat_percent <= 100.0:
            raise ValueError("VAT must be between 0 and 100 percent")
        return self

    def option_values(self) -> dict[str, float]:
        return {
            CONF_SPOT_EUR_CZK: self.eur_czk,
            CONF_SPOT_SUPPLIER_FEE: self.supplier_fee_czk_kwh,
            CONF_SPOT_VARIABLE_ADDITIONS: self.variable_additions_czk_kwh,
            CONF_SPOT_VAT_PERCENT: self.vat_percent,
        }

    def as_dict(self) -> dict[str, float]:
        return asdict(self)
