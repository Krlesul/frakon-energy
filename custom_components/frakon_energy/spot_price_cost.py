"""End-user spot electricity price calculation for FRAKON Energy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SpotPriceCostConfig:
    """Configurable additions applied to the wholesale spot price."""

    eur_czk: float
    supplier_fee_czk_kwh: float = 0.0
    variable_additions_czk_kwh: float = 0.0
    vat_percent: float = 21.0

    def __post_init__(self) -> None:
        if self.eur_czk <= 0:
            raise ValueError("eur_czk must be positive")
        if self.vat_percent < 0:
            raise ValueError("vat_percent must not be negative")


def calculate_spot_cost(
    price_eur_mwh: float,
    config: SpotPriceCostConfig,
) -> dict[str, Any]:
    """Convert wholesale EUR/MWh to a transparent CZK/kWh customer price.

    Regulated tariff components are deliberately not guessed here. They can be
    supplied through ``variable_additions_czk_kwh`` once the user's tariff and
    distributor configuration is known.
    """
    wholesale_czk_kwh = price_eur_mwh * config.eur_czk / 1000.0
    before_vat = (
        wholesale_czk_kwh
        + config.supplier_fee_czk_kwh
        + config.variable_additions_czk_kwh
    )
    vat_czk_kwh = before_vat * config.vat_percent / 100.0
    total_czk_kwh = before_vat + vat_czk_kwh
    return {
        "wholesale_czk_kwh": wholesale_czk_kwh,
        "supplier_fee_czk_kwh": config.supplier_fee_czk_kwh,
        "variable_additions_czk_kwh": config.variable_additions_czk_kwh,
        "vat_percent": config.vat_percent,
        "vat_czk_kwh": vat_czk_kwh,
        "total_czk_kwh": total_czk_kwh,
        "eur_czk": config.eur_czk,
    }
