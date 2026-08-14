"""Fail-closed assembly of supplier-commercial and regulated tariff components."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
import re

from .pricing import FixedPriceComponent, PriceComponentKind, VariablePriceComponent
from .regulated_pricing import RegulatedTariffBundle
from .tariff_provenance import MultiSourceTariffProvenance
from .tariff_sources import PRICE_SCOPE_ALL_IN

_TARIFF_RE = re.compile(r"^D\d{2}d$")
_BREAKER_RE = re.compile(r"^(?:1|3)x[1-9]\d*A$")


def _non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value.strip()


def _normalize_tariff(value: str) -> str:
    tariff = _non_empty(value, "distribution_tariff")
    tariff = tariff[0].upper() + tariff[1:-1] + tariff[-1].lower()
    if not _TARIFF_RE.fullmatch(tariff):
        raise ValueError("distribution_tariff must use a code such as D25d")
    return tariff


def _validate_breaker(value: str) -> str:
    breaker = _non_empty(value, "breaker_code")
    if not _BREAKER_RE.fullmatch(breaker):
        raise ValueError("breaker_code must use a code such as 3x25A")
    return breaker


def _period_intersection(
    starts: tuple[date, ...], ends: tuple[date | None, ...]
) -> tuple[date, date | None]:
    if not starts or any(not isinstance(item, date) for item in starts):
        raise ValueError("all validity starts must be dates")
    if any(item is not None and not isinstance(item, date) for item in ends):
        raise ValueError("all validity ends must be dates when provided")
    start = max(starts)
    bounded = [item for item in ends if item is not None]
    end = min(bounded) if bounded else None
    if end is not None and end < start:
        raise ValueError("commercial, regulated and provenance validity periods do not overlap")
    return start, end


@dataclass(frozen=True, slots=True)
class AllInTariffAssembly:
    """Technically complete all-in tariff candidate with multi-source provenance.

    This object is complete enough to calculate a customer price, but it is still
    separate from durable tariff-catalog confirmation.  Persisting/activating the
    result remains a later explicit step.
    """

    supplier: str
    product_name: str
    distribution_tariff: str
    breaker_code: str
    valid_from: date
    valid_to: date | None
    variable_components: tuple[VariablePriceComponent, ...]
    fixed_components: tuple[FixedPriceComponent, ...]
    provenance: MultiSourceTariffProvenance
    price_scope: str = field(default=PRICE_SCOPE_ALL_IN, init=False)
    all_in_ready: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "supplier", _non_empty(self.supplier, "supplier"))
        object.__setattr__(self, "product_name", _non_empty(self.product_name, "product_name"))
        object.__setattr__(self, "distribution_tariff", _normalize_tariff(self.distribution_tariff))
        object.__setattr__(self, "breaker_code", _validate_breaker(self.breaker_code))
        if not isinstance(self.valid_from, date):
            raise ValueError("valid_from must be a date")
        if self.valid_to is not None:
            if not isinstance(self.valid_to, date):
                raise ValueError("valid_to must be a date")
            if self.valid_to < self.valid_from:
                raise ValueError("assembly validity end must not precede start")
        if not isinstance(self.provenance, MultiSourceTariffProvenance):
            raise ValueError("provenance must be MultiSourceTariffProvenance")

        variable = tuple(self.variable_components)
        fixed = tuple(self.fixed_components)
        if not variable or not fixed:
            raise ValueError("all-in assembly requires variable and fixed components")
        if not all(isinstance(item, VariablePriceComponent) for item in variable):
            raise ValueError("variable_components contains an invalid item")
        if not all(isinstance(item, FixedPriceComponent) for item in fixed):
            raise ValueError("fixed_components contains an invalid item")
        names = [item.name for item in (*variable, *fixed)]
        if len(set(names)) != len(names):
            raise ValueError("all-in component names must be unique")
        object.__setattr__(self, "variable_components", variable)
        object.__setattr__(self, "fixed_components", fixed)

        if not self.provenance.covers_day(self.valid_from):
            raise ValueError("provenance does not cover assembly valid_from")
        if self.valid_to is not None and not self.provenance.covers_day(self.valid_to):
            raise ValueError("provenance does not cover assembly valid_to")

    @property
    def all_in_vt_czk_kwh(self) -> Decimal:
        return sum(
            (item.gross_high_rate_czk_per_kwh for item in self.variable_components),
            Decimal("0"),
        )

    @property
    def all_in_nt_czk_kwh(self) -> Decimal:
        return sum(
            (item.gross_low_rate_czk_per_kwh for item in self.variable_components),
            Decimal("0"),
        )

    @property
    def fixed_monthly_total_czk(self) -> Decimal:
        return sum(
            (item.gross_monthly_czk for item in self.fixed_components), Decimal("0")
        )


def assemble_all_in_tariff(
    *,
    supplier: str,
    product_name: str,
    distribution_tariff: str,
    breaker_code: str,
    commercial_valid_from: date,
    commodity: VariablePriceComponent,
    supplier_fixed: FixedPriceComponent,
    regulated: RegulatedTariffBundle,
    provenance: MultiSourceTariffProvenance,
    commercial_valid_to: date | None = None,
) -> AllInTariffAssembly:
    """Combine independently verified commercial and regulated tariff parts.

    The function refuses to assemble until the regulated bundle is explicitly
    confirmed and all tariff/breaker/validity boundaries agree.  It also refuses
    supplier components of the wrong semantic kind.
    """

    tariff = _normalize_tariff(distribution_tariff)
    breaker = _validate_breaker(breaker_code)
    if not isinstance(commercial_valid_from, date):
        raise ValueError("commercial_valid_from must be a date")
    if commercial_valid_to is not None:
        if not isinstance(commercial_valid_to, date):
            raise ValueError("commercial_valid_to must be a date")
        if commercial_valid_to < commercial_valid_from:
            raise ValueError("commercial validity end must not precede start")
    if not isinstance(commodity, VariablePriceComponent):
        raise ValueError("commodity must be VariablePriceComponent")
    if commodity.kind != PriceComponentKind.COMMODITY:
        raise ValueError("commercial variable component must be COMMODITY")
    if not isinstance(supplier_fixed, FixedPriceComponent):
        raise ValueError("supplier_fixed must be FixedPriceComponent")
    if supplier_fixed.kind != PriceComponentKind.SUPPLIER_FIXED:
        raise ValueError("commercial fixed component must be SUPPLIER_FIXED")
    if not isinstance(regulated, RegulatedTariffBundle):
        raise ValueError("regulated must be RegulatedTariffBundle")
    if not regulated.confirmed:
        raise ValueError("regulated tariff bundle must be confirmed before all-in assembly")
    if not isinstance(provenance, MultiSourceTariffProvenance):
        raise ValueError("provenance must be MultiSourceTariffProvenance")

    if regulated.distribution_tariff != tariff:
        raise ValueError("commercial and regulated distribution tariffs do not match")
    if regulated.breaker_code != breaker:
        raise ValueError("regulated breaker does not match customer breaker")

    valid_from, valid_to = _period_intersection(
        (commercial_valid_from, regulated.valid_from, provenance.valid_from),
        (commercial_valid_to, regulated.valid_to, provenance.valid_to),
    )
    if not regulated.matches_customer_tariff(
        distribution_tariff=tariff,
        breaker_code=breaker,
        day=valid_from,
    ):
        raise ValueError("regulated tariff does not match the assembly validity start")

    return AllInTariffAssembly(
        supplier=supplier,
        product_name=product_name,
        distribution_tariff=tariff,
        breaker_code=breaker,
        valid_from=valid_from,
        valid_to=valid_to,
        variable_components=(commodity, *regulated.variable_components),
        fixed_components=(supplier_fixed, *regulated.fixed_components),
        provenance=provenance,
    )
