from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Iterable


class Supplier(StrEnum):
    CEZ = "cez"
    EON = "eon"
    PRE = "pre"
    MND = "mnd"
    INNOGY = "innogy"
    CENTROPOL = "centropol"
    EPET = "epet"
    OTHER = "other"


class Distributor(StrEnum):
    CEZ_DISTRIBUCE = "cez_distribuce"
    EG_D = "eg_d"
    PRE_DISTRIBUCE = "pre_distribuce"


class ContractKind(StrEnum):
    FIXED = "fixed"
    INDEFINITE = "indefinite"
    SPOT = "spot"


@dataclass(frozen=True, slots=True)
class Breaker:
    phases: int
    amperes: int

    def __post_init__(self) -> None:
        if self.phases not in (1, 3):
            raise ValueError("Breaker must have one or three phases")
        if self.amperes <= 0:
            raise ValueError("Breaker amperage must be positive")

    @property
    def code(self) -> str:
        return f"{self.phases}x{self.amperes}A"


@dataclass(frozen=True, slots=True)
class ElectricityContract:
    supplier: Supplier
    distributor: Distributor
    product_name: str
    contract_kind: ContractKind
    distribution_tariff: str
    breaker: Breaker
    valid_from: date
    valid_to: date | None = None
    fixation_end: date | None = None
    customer_confirmed: bool = False

    def __post_init__(self) -> None:
        tariff = self.distribution_tariff.strip().upper()
        if not tariff.startswith("D") or not tariff[1:-1].isdigit() or tariff[-1] != "D":
            raise ValueError("Distribution tariff must use a code such as D25d")
        if not self.product_name.strip():
            raise ValueError("Product name must not be empty")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("Contract end must not precede contract start")
        if self.contract_kind == ContractKind.FIXED and self.fixation_end is None:
            raise ValueError("Fixed contract requires fixation end date")
        if self.fixation_end is not None and self.fixation_end < self.valid_from:
            raise ValueError("Fixation end must not precede contract start")
        object.__setattr__(self, "distribution_tariff", tariff[:-1] + "d")

    def applies_on(self, day: date) -> bool:
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)

    def days_until_fixation_end(self, day: date) -> int | None:
        if self.fixation_end is None:
            return None
        return (self.fixation_end - day).days


@dataclass(frozen=True, slots=True)
class TariffLookupKey:
    supplier: Supplier
    distributor: Distributor
    product_name: str
    contract_kind: ContractKind
    distribution_tariff: str
    breaker_code: str
    valid_on: date


def lookup_key(contract: ElectricityContract, day: date) -> TariffLookupKey:
    return TariffLookupKey(
        supplier=contract.supplier,
        distributor=contract.distributor,
        product_name=contract.product_name.strip(),
        contract_kind=contract.contract_kind,
        distribution_tariff=contract.distribution_tariff,
        breaker_code=contract.breaker.code,
        valid_on=day,
    )


def select_contract_for_day(
    contracts: Iterable[ElectricityContract], day: date
) -> ElectricityContract:
    matches = [contract for contract in contracts if contract.applies_on(day)]
    if not matches:
        raise LookupError(f"No electricity contract applies on {day.isoformat()}")
    return max(matches, key=lambda contract: contract.valid_from)
