"""Map parsed ČEZ supplier-commercial prices into FRAKON pricing components."""

from __future__ import annotations

from dataclasses import dataclass

from ..pricing import FixedPriceComponent, PriceComponentKind, VariablePriceComponent
from .cez_tariff_parser import ParsedCezCommercialPrice


@dataclass(frozen=True, slots=True)
class CezCommercialComponents:
    """Supplier-only components that are not an all-in tariff on their own."""

    commodity: VariablePriceComponent
    supplier_fixed: FixedPriceComponent


def components_from_parsed_cez_commercial_price(
    parsed: ParsedCezCommercialPrice,
) -> CezCommercialComponents:
    """Convert a parsed dual-rate ČEZ document into supplier pricing components.

    Single-rate tariffs intentionally fail here.  The current all-in pricing model
    requires explicit VT and NT component values, so silently copying VT into NT
    or replacing an unavailable NT with zero would produce misleading costs.
    """
    if not isinstance(parsed, ParsedCezCommercialPrice):
        raise ValueError("parsed must be ParsedCezCommercialPrice")
    if parsed.low_rate_czk_per_kwh is None:
        raise ValueError(
            "single-rate ČEZ tariff cannot be mapped to dual-rate components safely"
        )
    if parsed.includes_vat is not True:
        raise ValueError("parsed ČEZ commercial price must include VAT")

    return CezCommercialComponents(
        commodity=VariablePriceComponent(
            kind=PriceComponentKind.COMMODITY,
            name="ČEZ – obchodní cena elektřiny",
            high_rate_czk_per_kwh=parsed.high_rate_czk_per_kwh,
            low_rate_czk_per_kwh=parsed.low_rate_czk_per_kwh,
            includes_vat=True,
        ),
        supplier_fixed=FixedPriceComponent(
            kind=PriceComponentKind.SUPPLIER_FIXED,
            name="ČEZ – stálá platba dodavatele",
            monthly_czk=parsed.supplier_standing_czk_month,
            includes_vat=True,
        ),
    )
