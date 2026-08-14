from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
import hashlib
import json
from typing import Any, Iterable, Mapping

CONTRACT_SCHEMA_VERSION = 1
OPTION_ELECTRICITY_CONTRACTS = "electricity_contracts"


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


def _date_from_value(value: Any, field: str) -> date:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 date")
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"{field} must be an ISO-8601 date") from err


@dataclass(frozen=True, slots=True)
class Breaker:
    phases: int
    amperes: int

    def __post_init__(self) -> None:
        if isinstance(self.phases, bool) or not isinstance(self.phases, int) or self.phases not in (1, 3):
            raise ValueError("Breaker must have one or three phases")
        if isinstance(self.amperes, bool) or not isinstance(self.amperes, int) or self.amperes <= 0:
            raise ValueError("Breaker amperage must be a positive integer")

    @property
    def code(self) -> str:
        return f"{self.phases}x{self.amperes}A"

    def as_dict(self) -> dict[str, int]:
        return {"phases": self.phases, "amperes": self.amperes}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Breaker:
        if not isinstance(value, Mapping):
            raise ValueError("breaker must be an object")
        phases = value.get("phases")
        amperes = value.get("amperes")
        if isinstance(phases, bool) or isinstance(amperes, bool):
            raise ValueError("breaker values must be integers")
        try:
            return cls(phases=int(phases), amperes=int(amperes))
        except (TypeError, ValueError) as err:
            if isinstance(err, ValueError) and str(err).startswith("Breaker"):
                raise
            raise ValueError("breaker values must be integers") from err


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
        if not isinstance(self.supplier, Supplier):
            raise ValueError("supplier must be Supplier")
        if not isinstance(self.distributor, Distributor):
            raise ValueError("distributor must be Distributor")
        if not isinstance(self.contract_kind, ContractKind):
            raise ValueError("contract_kind must be ContractKind")
        if not isinstance(self.breaker, Breaker):
            raise ValueError("breaker must be Breaker")
        if not isinstance(self.product_name, str) or not self.product_name.strip():
            raise ValueError("Product name must not be empty")
        if not isinstance(self.distribution_tariff, str):
            raise ValueError("Distribution tariff must use a code such as D25d")
        tariff = self.distribution_tariff.strip().upper()
        if len(tariff) < 3 or not tariff.startswith("D") or not tariff[1:-1].isdigit() or tariff[-1] != "D":
            raise ValueError("Distribution tariff must use a code such as D25d")
        if not isinstance(self.valid_from, date):
            raise ValueError("valid_from must be a date")
        if self.valid_to is not None:
            if not isinstance(self.valid_to, date):
                raise ValueError("valid_to must be a date")
            if self.valid_to < self.valid_from:
                raise ValueError("Contract end must not precede contract start")
        if self.contract_kind == ContractKind.FIXED and self.fixation_end is None:
            raise ValueError("Fixed contract requires fixation end date")
        if self.fixation_end is not None:
            if not isinstance(self.fixation_end, date):
                raise ValueError("fixation_end must be a date")
            if self.fixation_end < self.valid_from:
                raise ValueError("Fixation end must not precede contract start")
        if not isinstance(self.customer_confirmed, bool):
            raise ValueError("customer_confirmed must be boolean")
        object.__setattr__(self, "distribution_tariff", tariff[:-1] + "d")

    def applies_on(self, day: date) -> bool:
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)

    def days_until_fixation_end(self, day: date) -> int | None:
        if self.fixation_end is None:
            return None
        return (self.fixation_end - day).days

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "supplier": self.supplier.value,
            "distributor": self.distributor.value,
            "product_name": self.product_name,
            "contract_kind": self.contract_kind.value,
            "distribution_tariff": self.distribution_tariff,
            "breaker": self.breaker.as_dict(),
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to is not None else None,
            "fixation_end": self.fixation_end.isoformat() if self.fixation_end is not None else None,
            "customer_confirmed": self.customer_confirmed,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ElectricityContract:
        if not isinstance(value, Mapping):
            raise ValueError("electricity contract must be an object")
        if value.get("schema_version") != CONTRACT_SCHEMA_VERSION:
            raise ValueError("unsupported electricity contract schema version")
        try:
            supplier = Supplier(str(value["supplier"]))
            distributor = Distributor(str(value["distributor"]))
            contract_kind = ContractKind(str(value["contract_kind"]))
        except (KeyError, ValueError) as err:
            raise ValueError("invalid electricity contract enum value") from err
        valid_to_raw = value.get("valid_to")
        fixation_end_raw = value.get("fixation_end")
        return cls(
            supplier=supplier,
            distributor=distributor,
            product_name=value.get("product_name"),
            contract_kind=contract_kind,
            distribution_tariff=value.get("distribution_tariff"),
            breaker=Breaker.from_dict(value.get("breaker")),
            valid_from=_date_from_value(value.get("valid_from"), "valid_from"),
            valid_to=(
                _date_from_value(valid_to_raw, "valid_to")
                if valid_to_raw not in (None, "")
                else None
            ),
            fixation_end=(
                _date_from_value(fixation_end_raw, "fixation_end")
                if fixation_end_raw not in (None, "")
                else None
            ),
            customer_confirmed=value.get("customer_confirmed", False),
        )


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


def _select_unique_newest_contract(
    matches: list[ElectricityContract],
    day: date,
    *,
    confirmed: bool,
) -> ElectricityContract:
    if not matches:
        prefix = "confirmed " if confirmed else ""
        raise LookupError(
            f"No {prefix}electricity contract applies on {day.isoformat()}"
        )
    newest_valid_from = max(contract.valid_from for contract in matches)
    newest = [contract for contract in matches if contract.valid_from == newest_valid_from]
    if len(newest) != 1:
        prefix = "confirmed " if confirmed else ""
        raise ValueError(
            f"ambiguous {prefix}electricity contracts for {day.isoformat()}"
        )
    return newest[0]


def select_contract_for_day(
    contracts: Iterable[ElectricityContract], day: date
) -> ElectricityContract:
    matches = [contract for contract in contracts if contract.applies_on(day)]
    return _select_unique_newest_contract(matches, day, confirmed=False)


def select_confirmed_contract_for_day(
    contracts: Iterable[ElectricityContract], day: date
) -> ElectricityContract:
    matches = [
        contract
        for contract in contracts
        if contract.customer_confirmed and contract.applies_on(day)
    ]
    return _select_unique_newest_contract(matches, day, confirmed=True)


def contract_fingerprint(contract: ElectricityContract) -> str:
    """Return stable content identity that intentionally ignores confirmation state."""
    if not isinstance(contract, ElectricityContract):
        raise ValueError("contract must be ElectricityContract")
    payload = contract.as_dict()
    payload["customer_confirmed"] = False
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def contracts_from_options(options: Mapping[str, Any]) -> tuple[ElectricityContract, ...]:
    raw = options.get(OPTION_ELECTRICITY_CONTRACTS, [])
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("electricity_contracts must be a list")

    contracts: list[ElectricityContract] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("each electricity contract must be an object")
        contract = ElectricityContract.from_dict(item)
        fingerprint = contract_fingerprint(contract)
        if fingerprint in seen:
            raise ValueError(f"duplicate electricity contract fingerprint: {fingerprint}")
        seen.add(fingerprint)
        contracts.append(contract)
    return tuple(contracts)


def append_electricity_contract(
    options: Mapping[str, Any], contract: ElectricityContract
) -> dict[str, Any]:
    """Append one immutable contract version without overwriting history."""
    if not isinstance(contract, ElectricityContract):
        raise ValueError("contract must be ElectricityContract")
    contracts = list(contracts_from_options(options))
    fingerprint = contract_fingerprint(contract)
    if any(contract_fingerprint(item) == fingerprint for item in contracts):
        return dict(options)
    contracts.append(contract)
    updated = dict(options)
    updated[OPTION_ELECTRICITY_CONTRACTS] = [item.as_dict() for item in contracts]
    return updated


def confirm_electricity_contract(
    options: Mapping[str, Any], fingerprint: str
) -> dict[str, Any]:
    """Confirm exactly one stored contract without changing its content identity."""
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(char not in "0123456789abcdef" for char in fingerprint)
    ):
        raise ValueError("fingerprint must be a lowercase SHA-256 hex digest")

    contracts = list(contracts_from_options(options))
    matched = False
    for index, contract in enumerate(contracts):
        if contract_fingerprint(contract) != fingerprint:
            continue
        matched = True
        if not contract.customer_confirmed:
            contracts[index] = replace(contract, customer_confirmed=True)
        break
    if not matched:
        raise LookupError(f"electricity contract not found: {fingerprint}")

    updated = dict(options)
    updated[OPTION_ELECTRICITY_CONTRACTS] = [item.as_dict() for item in contracts]
    return updated


def confirmed_contract_from_options(
    options: Mapping[str, Any], day: date
) -> ElectricityContract:
    return select_confirmed_contract_for_day(contracts_from_options(options), day)
