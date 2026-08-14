"""Two-phase regulated tariff proposal and confirmation persistence."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
from typing import Any, Mapping

from .regulated_catalog import (
    ConfirmedRegulatedTariffVersion,
    _bundle_from_dict,
    _bundle_to_dict,
    _validate_evidence,
    append_confirmed_regulated_tariff,
)
from .regulated_pricing import RegulatedTariffBundle
from .tariff_provenance import PriceEvidence, price_evidence_fingerprint

REGULATED_PROPOSAL_SCHEMA_VERSION = 1
OPTION_REGULATED_TARIFF_PROPOSALS = "regulated_tariff_proposals"


def _validate_fingerprint(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError("proposal fingerprint must be a lowercase SHA-256 hex digest")
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


def regulated_proposal_fingerprint(
    bundle: RegulatedTariffBundle,
    evidence: tuple[PriceEvidence, ...],
) -> str:
    """Stable pricing identity for one unconfirmed regulator proposal."""
    if not isinstance(bundle, RegulatedTariffBundle):
        raise ValueError("bundle must be RegulatedTariffBundle")
    if bundle.confirmed is not False:
        raise ValueError("regulated proposal bundle must remain unconfirmed")
    evidence = tuple(evidence)
    _validate_evidence(bundle, evidence)
    payload = {
        "schema_version": REGULATED_PROPOSAL_SCHEMA_VERSION,
        "bundle": _bundle_to_dict(bundle),
        "evidence": [
            item.as_dict()
            for item in sorted(evidence, key=price_evidence_fingerprint)
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class RegulatedTariffProposal:
    """Immutable unconfirmed regulator proposal awaiting explicit confirmation."""

    bundle: RegulatedTariffBundle
    evidence: tuple[PriceEvidence, ...]
    proposed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.bundle, RegulatedTariffBundle):
            raise ValueError("bundle must be RegulatedTariffBundle")
        if self.bundle.confirmed is not False:
            raise ValueError("regulated proposal bundle must remain unconfirmed")
        evidence = tuple(self.evidence)
        _validate_evidence(self.bundle, evidence)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "proposed_at", _aware_datetime(self.proposed_at, "proposed_at"))

    @property
    def fingerprint(self) -> str:
        return regulated_proposal_fingerprint(self.bundle, self.evidence)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REGULATED_PROPOSAL_SCHEMA_VERSION,
            "fingerprint": self.fingerprint,
            "proposed_at": self.proposed_at.isoformat(),
            "bundle": _bundle_to_dict(self.bundle),
            "evidence": [item.as_dict() for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RegulatedTariffProposal":
        if not isinstance(value, Mapping):
            raise ValueError("regulated tariff proposal must be an object")
        if value.get("schema_version") != REGULATED_PROPOSAL_SCHEMA_VERSION:
            raise ValueError("unsupported regulated proposal schema version")
        raw_evidence = value.get("evidence")
        if not isinstance(raw_evidence, list):
            raise ValueError("regulated proposal evidence must be a list")
        proposal = cls(
            bundle=_bundle_from_dict(value.get("bundle")),
            evidence=tuple(PriceEvidence.from_dict(item) for item in raw_evidence),
            proposed_at=_aware_datetime(value.get("proposed_at"), "proposed_at"),
        )
        if value.get("fingerprint") != proposal.fingerprint:
            raise ValueError("regulated proposal fingerprint mismatch")
        return proposal


def regulated_tariff_proposal_from_payload(
    bundle_payload: Mapping[str, Any],
    evidence_payload: list[Any],
    *,
    proposed_at: datetime,
) -> RegulatedTariffProposal:
    """Build a server-timestamped unconfirmed proposal from validated payload objects."""
    if not isinstance(bundle_payload, Mapping):
        raise ValueError("regulated proposal bundle must be an object")
    if not isinstance(evidence_payload, list):
        raise ValueError("regulated proposal evidence must be a list")
    return RegulatedTariffProposal(
        bundle=_bundle_from_dict(bundle_payload),
        evidence=tuple(PriceEvidence.from_dict(item) for item in evidence_payload),
        proposed_at=proposed_at,
    )


def regulated_tariff_proposals_from_options(
    options: Mapping[str, Any],
) -> tuple[RegulatedTariffProposal, ...]:
    raw = options.get(OPTION_REGULATED_TARIFF_PROPOSALS, [])
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("regulated_tariff_proposals must be a list")
    proposals: list[RegulatedTariffProposal] = []
    seen: set[str] = set()
    for item in raw:
        proposal = RegulatedTariffProposal.from_dict(item)
        if proposal.fingerprint in seen:
            raise ValueError(
                f"duplicate regulated proposal fingerprint: {proposal.fingerprint}"
            )
        seen.add(proposal.fingerprint)
        proposals.append(proposal)
    return tuple(proposals)


def append_regulated_tariff_proposal(
    options: Mapping[str, Any],
    proposal: RegulatedTariffProposal,
) -> dict[str, Any]:
    """Append an immutable unconfirmed proposal without overwriting history."""
    if not isinstance(proposal, RegulatedTariffProposal):
        raise ValueError("proposal must be RegulatedTariffProposal")
    proposals = list(regulated_tariff_proposals_from_options(options))
    if any(item.fingerprint == proposal.fingerprint for item in proposals):
        return dict(options)
    proposals.append(proposal)
    updated = dict(options)
    updated[OPTION_REGULATED_TARIFF_PROPOSALS] = [item.as_dict() for item in proposals]
    return updated


def confirm_regulated_tariff_proposal(
    options: Mapping[str, Any],
    proposal_fingerprint: str,
) -> tuple[dict[str, Any], ConfirmedRegulatedTariffVersion]:
    """Confirm exactly one already-stored proposal by fingerprint only."""
    fingerprint = _validate_fingerprint(proposal_fingerprint)
    proposals = regulated_tariff_proposals_from_options(options)
    proposal = next((item for item in proposals if item.fingerprint == fingerprint), None)
    if proposal is None:
        raise LookupError(f"regulated tariff proposal not found: {fingerprint}")

    confirmed_bundle = replace(proposal.bundle, confirmed=True)
    version = ConfirmedRegulatedTariffVersion(
        bundle=confirmed_bundle,
        evidence=proposal.evidence,
    )
    return append_confirmed_regulated_tariff(options, version), version
