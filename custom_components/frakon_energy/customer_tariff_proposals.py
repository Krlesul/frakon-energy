"""Durable customer tariff proposals linking contract and verified all-in pricing."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
import hashlib
import json
from typing import Any, Mapping
import unicodedata

from .all_in_catalog import (
    PersistedAllInTariff,
    all_in_tariff_fingerprint,
    all_in_tariffs_from_options,
    append_all_in_tariff,
    confirm_all_in_tariff,
)
from .contracts import (
    ElectricityContract,
    append_electricity_contract,
    confirm_electricity_contract,
    contract_fingerprint,
    contracts_from_options,
)
from .regulated_catalog import confirmed_regulated_versions_from_options
from .tariff_assembly import AllInTariffAssembly
from .tariff_sources import PRICE_SCOPE_REGULATED

CUSTOMER_TARIFF_PROPOSAL_SCHEMA_VERSION = 1
OPTION_CUSTOMER_TARIFF_PROPOSALS = "customer_tariff_proposals"


def _digest(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _aware_datetime(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as err:
            raise ValueError(f"{field} must be an ISO-8601 datetime") from err
    else:
        raise ValueError(f"{field} must be an ISO-8601 datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return parsed


def _date_value(value: Any, field: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value)
        except ValueError as err:
            raise ValueError(f"{field} must be an ISO-8601 date") from err
    raise ValueError(f"{field} must be an ISO-8601 date")


def _supplier_identity(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("supplier must not be empty")
    decomposed = unicodedata.normalize("NFKD", value.strip()).casefold()
    normalized = "".join(char for char in decomposed if char.isalnum())
    if not normalized:
        raise ValueError("supplier must contain an alphanumeric identity")
    return normalized


@dataclass(frozen=True, slots=True)
class CustomerTariffProposal:
    """Immutable references to one server-verified customer tariff proposal."""

    contract_fingerprint: str
    all_in_tariff_fingerprint: str
    candidate_fingerprint: str
    regulated_version_fingerprint: str
    proposed_for_day: date
    proposed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "contract_fingerprint",
            _digest(self.contract_fingerprint, "contract_fingerprint"),
        )
        object.__setattr__(
            self,
            "all_in_tariff_fingerprint",
            _digest(self.all_in_tariff_fingerprint, "all_in_tariff_fingerprint"),
        )
        object.__setattr__(
            self,
            "candidate_fingerprint",
            _digest(self.candidate_fingerprint, "candidate_fingerprint"),
        )
        object.__setattr__(
            self,
            "regulated_version_fingerprint",
            _digest(self.regulated_version_fingerprint, "regulated_version_fingerprint"),
        )
        object.__setattr__(
            self,
            "proposed_for_day",
            _date_value(self.proposed_for_day, "proposed_for_day"),
        )
        object.__setattr__(
            self,
            "proposed_at",
            _aware_datetime(self.proposed_at, "proposed_at"),
        )

    @property
    def fingerprint(self) -> str:
        payload = {
            "schema_version": CUSTOMER_TARIFF_PROPOSAL_SCHEMA_VERSION,
            "contract_fingerprint": self.contract_fingerprint,
            "all_in_tariff_fingerprint": self.all_in_tariff_fingerprint,
            "candidate_fingerprint": self.candidate_fingerprint,
            "regulated_version_fingerprint": self.regulated_version_fingerprint,
            "proposed_for_day": self.proposed_for_day.isoformat(),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CUSTOMER_TARIFF_PROPOSAL_SCHEMA_VERSION,
            "fingerprint": self.fingerprint,
            "contract_fingerprint": self.contract_fingerprint,
            "all_in_tariff_fingerprint": self.all_in_tariff_fingerprint,
            "candidate_fingerprint": self.candidate_fingerprint,
            "regulated_version_fingerprint": self.regulated_version_fingerprint,
            "proposed_for_day": self.proposed_for_day.isoformat(),
            "proposed_at": self.proposed_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CustomerTariffProposal":
        if not isinstance(value, Mapping):
            raise ValueError("customer tariff proposal must be an object")
        if value.get("schema_version") != CUSTOMER_TARIFF_PROPOSAL_SCHEMA_VERSION:
            raise ValueError("unsupported customer tariff proposal schema version")
        proposal = cls(
            contract_fingerprint=value.get("contract_fingerprint"),
            all_in_tariff_fingerprint=value.get("all_in_tariff_fingerprint"),
            candidate_fingerprint=value.get("candidate_fingerprint"),
            regulated_version_fingerprint=value.get("regulated_version_fingerprint"),
            proposed_for_day=_date_value(value.get("proposed_for_day"), "proposed_for_day"),
            proposed_at=_aware_datetime(value.get("proposed_at"), "proposed_at"),
        )
        if value.get("fingerprint") != proposal.fingerprint:
            raise ValueError("customer tariff proposal fingerprint mismatch")
        return proposal


def customer_tariff_proposals_from_options(
    options: Mapping[str, Any],
) -> tuple[CustomerTariffProposal, ...]:
    raw = options.get(OPTION_CUSTOMER_TARIFF_PROPOSALS, [])
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("customer_tariff_proposals must be a list")
    proposals: list[CustomerTariffProposal] = []
    seen: set[str] = set()
    for item in raw:
        proposal = CustomerTariffProposal.from_dict(item)
        if proposal.fingerprint in seen:
            raise ValueError(
                f"duplicate customer tariff proposal fingerprint: {proposal.fingerprint}"
            )
        seen.add(proposal.fingerprint)
        proposals.append(proposal)
    return tuple(proposals)


def append_customer_tariff_proposal(
    options: Mapping[str, Any],
    proposal: CustomerTariffProposal,
) -> dict[str, Any]:
    if not isinstance(proposal, CustomerTariffProposal):
        raise ValueError("proposal must be CustomerTariffProposal")
    proposals = list(customer_tariff_proposals_from_options(options))
    if any(item.fingerprint == proposal.fingerprint for item in proposals):
        return dict(options)
    proposals.append(proposal)
    updated = dict(options)
    updated[OPTION_CUSTOMER_TARIFF_PROPOSALS] = [item.as_dict() for item in proposals]
    return updated


def stage_customer_tariff_proposal(
    options: Mapping[str, Any],
    *,
    contract: ElectricityContract,
    assembly: AllInTariffAssembly,
    candidate_fingerprint: str,
    regulated_version_fingerprint: str,
    proposed_for_day: date,
    proposed_at: datetime,
) -> tuple[dict[str, Any], CustomerTariffProposal]:
    """Atomically stage unconfirmed contract/all-in records and their immutable link."""
    if not isinstance(contract, ElectricityContract):
        raise ValueError("contract must be ElectricityContract")
    if not isinstance(assembly, AllInTariffAssembly):
        raise ValueError("assembly must be AllInTariffAssembly")
    day = _date_value(proposed_for_day, "proposed_for_day")
    if not contract.applies_on(day):
        raise ValueError("contract does not apply on proposed_for_day")
    if not (assembly.valid_from <= day and (assembly.valid_to is None or day <= assembly.valid_to)):
        raise ValueError("all-in tariff does not apply on proposed_for_day")
    if _supplier_identity(assembly.supplier) != _supplier_identity(contract.supplier.value):
        raise ValueError("all-in tariff supplier does not match contract")
    if assembly.product_name.strip() != contract.product_name.strip():
        raise ValueError("all-in tariff product does not match contract")
    if assembly.distribution_tariff != contract.distribution_tariff:
        raise ValueError("all-in distribution tariff does not match contract")
    if assembly.breaker_code != contract.breaker.code:
        raise ValueError("all-in breaker does not match contract")

    unconfirmed_contract = replace(contract, customer_confirmed=False)
    contract_fp = contract_fingerprint(unconfirmed_contract)
    all_in_item = PersistedAllInTariff(assembly=assembly, confirmed=False)
    all_in_fp = all_in_tariff_fingerprint(all_in_item)
    proposal = CustomerTariffProposal(
        contract_fingerprint=contract_fp,
        all_in_tariff_fingerprint=all_in_fp,
        candidate_fingerprint=_digest(candidate_fingerprint, "candidate_fingerprint"),
        regulated_version_fingerprint=_digest(
            regulated_version_fingerprint,
            "regulated_version_fingerprint",
        ),
        proposed_for_day=day,
        proposed_at=proposed_at,
    )

    updated = append_electricity_contract(options, unconfirmed_contract)
    updated = append_all_in_tariff(updated, assembly)
    # Validate the complete immutable graph before the proposal itself becomes
    # durable. Failure returns no options object to the caller, so no partial state
    # can be written by the WebSocket boundary.
    _proposal_targets(updated, proposal)
    updated = append_customer_tariff_proposal(updated, proposal)
    return updated, proposal


def _proposal_targets(
    options: Mapping[str, Any],
    proposal: CustomerTariffProposal,
) -> tuple[ElectricityContract, PersistedAllInTariff]:
    contract = next(
        (
            item
            for item in contracts_from_options(options)
            if contract_fingerprint(item) == proposal.contract_fingerprint
        ),
        None,
    )
    if contract is None:
        raise LookupError("customer tariff proposal contract target was not found")
    all_in = next(
        (
            item
            for item in all_in_tariffs_from_options(options)
            if all_in_tariff_fingerprint(item) == proposal.all_in_tariff_fingerprint
        ),
        None,
    )
    if all_in is None:
        raise LookupError("customer tariff proposal all-in target was not found")
    regulated = next(
        (
            item
            for item in confirmed_regulated_versions_from_options(options)
            if item.fingerprint == proposal.regulated_version_fingerprint
        ),
        None,
    )
    if regulated is None:
        raise LookupError("customer tariff proposal regulator target was not found")

    day = proposal.proposed_for_day
    assembly = all_in.assembly
    if not contract.applies_on(day) or not all_in.applies_on(day):
        raise ValueError("customer tariff proposal targets do not cover proposed day")
    if _supplier_identity(assembly.supplier) != _supplier_identity(contract.supplier.value):
        raise ValueError("customer tariff proposal supplier linkage mismatch")
    if assembly.product_name.strip() != contract.product_name.strip():
        raise ValueError("customer tariff proposal product linkage mismatch")
    if assembly.distribution_tariff != contract.distribution_tariff:
        raise ValueError("customer tariff proposal distribution tariff linkage mismatch")
    if assembly.breaker_code != contract.breaker.code:
        raise ValueError("customer tariff proposal breaker linkage mismatch")
    if regulated.bundle.distributor != contract.distributor.value:
        raise ValueError("customer tariff proposal distributor linkage mismatch")
    if regulated.bundle.distribution_tariff != contract.distribution_tariff:
        raise ValueError("customer tariff proposal regulator tariff linkage mismatch")
    if regulated.bundle.breaker_code != contract.breaker.code:
        raise ValueError("customer tariff proposal regulator breaker linkage mismatch")
    if not regulated.bundle.applies_on(day):
        raise ValueError("customer tariff proposal regulator version does not cover proposed day")
    regulated_evidence = assembly.provenance.evidence_for_scope(PRICE_SCOPE_REGULATED)
    if not any(
        evidence.source_url == regulated.bundle.source_url
        and (
            regulated.bundle.checksum is None
            or evidence.checksum == regulated.bundle.checksum
        )
        for evidence in regulated_evidence
    ):
        raise ValueError("customer tariff proposal all-in provenance does not match regulator target")
    return contract, all_in


def confirm_customer_tariff_proposal(
    options: Mapping[str, Any],
    proposal_fingerprint: str,
) -> tuple[dict[str, Any], CustomerTariffProposal]:
    """Confirm linked contract and all-in price by one already-stored proposal fingerprint."""
    fingerprint = _digest(proposal_fingerprint, "proposal_fingerprint")
    proposal = next(
        (
            item
            for item in customer_tariff_proposals_from_options(options)
            if item.fingerprint == fingerprint
        ),
        None,
    )
    if proposal is None:
        raise LookupError(f"customer tariff proposal not found: {fingerprint}")

    # Validate every linked immutable record before calculating either confirmation
    # update, so a corrupt proposal cannot partially activate one side.
    _proposal_targets(options, proposal)
    updated = confirm_electricity_contract(options, proposal.contract_fingerprint)
    updated = confirm_all_in_tariff(updated, proposal.all_in_tariff_fingerprint)
    return updated, proposal
