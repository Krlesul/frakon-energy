"""Two-phase proposal/confirmation persistence for exact MND tariff sources.

A source proposal contains no tariff prices. It records only the exact official
MND document that the backend has already downloaded and SHA-256 validated,
bound to a hashed operational source context. Confirmation accepts only the
stored proposal fingerprint and appends an immutable confirmed resolver record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import re
from typing import Any, Mapping

from .mnd_confirmed_source_resolver import (
    ConfirmedMndSourceResolution,
    append_confirmed_mnd_source_resolution,
    confirmed_mnd_source_resolution_fingerprint,
)

MND_SOURCE_PROPOSALS_OPTION = "mnd_source_proposals"
MND_SOURCE_PROPOSAL_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _fingerprint(value: Any, field: str = "proposal_fingerprint") -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _aware_datetime(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value.strip():
        try:
            result = datetime.fromisoformat(value)
        except ValueError as err:
            raise ValueError(f"{field} must be an ISO-8601 datetime") from err
    else:
        raise ValueError(f"{field} must be an ISO-8601 datetime")
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{field} must include timezone information")
    return result


def _date_from_value(value: Any, field: str, *, optional: bool = False) -> date | None:
    if optional and value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 date")
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"{field} must be an ISO-8601 date") from err


@dataclass(frozen=True, slots=True)
class MndSourceProposal:
    """Immutable server-validated MND document proposal awaiting confirmation."""

    source_context_fingerprint: str
    product_name: str
    distributor: str
    contract_kind: str
    source_url: str
    valid_from: date
    valid_to: date | None
    document_sha256: str
    proposed_at: datetime
    document_date: date | None = None

    def __post_init__(self) -> None:
        proposed_at = _aware_datetime(self.proposed_at, "proposed_at")
        object.__setattr__(self, "proposed_at", proposed_at)

        # Reuse the confirmed resolver record's exact product, official-URL,
        # public-validity and SHA validation. This object is not persisted as
        # confirmed; it is only a validation/canonicalization boundary here.
        validated = ConfirmedMndSourceResolution(
            source_context_fingerprint=self.source_context_fingerprint,
            product_name=self.product_name,
            distributor=self.distributor,
            contract_kind=self.contract_kind,
            source_url=self.source_url,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
            document_date=self.document_date,
            document_sha256=self.document_sha256,
            confirmed_at=proposed_at,
        )
        object.__setattr__(
            self, "source_context_fingerprint", validated.source_context_fingerprint
        )
        object.__setattr__(self, "product_name", validated.product_name)
        object.__setattr__(self, "distributor", validated.distributor)
        object.__setattr__(self, "contract_kind", validated.contract_kind)
        object.__setattr__(self, "source_url", validated.source_url)
        object.__setattr__(self, "valid_from", validated.valid_from)
        object.__setattr__(self, "valid_to", validated.valid_to)
        object.__setattr__(self, "document_date", validated.document_date)
        object.__setattr__(self, "document_sha256", validated.document_sha256)

    @property
    def fingerprint(self) -> str:
        payload = {
            "schema_version": MND_SOURCE_PROPOSAL_SCHEMA_VERSION,
            "source_context_fingerprint": self.source_context_fingerprint,
            "product_name": self.product_name,
            "distributor": self.distributor,
            "contract_kind": self.contract_kind,
            "source_url": self.source_url,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to is not None else None,
            "document_date": (
                self.document_date.isoformat()
                if self.document_date is not None
                else None
            ),
            "document_sha256": self.document_sha256,
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
            "schema_version": MND_SOURCE_PROPOSAL_SCHEMA_VERSION,
            "fingerprint": self.fingerprint,
            "source_context_fingerprint": self.source_context_fingerprint,
            "product_name": self.product_name,
            "distributor": self.distributor,
            "contract_kind": self.contract_kind,
            "source_url": self.source_url,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to is not None else None,
            "document_date": (
                self.document_date.isoformat()
                if self.document_date is not None
                else None
            ),
            "document_sha256": self.document_sha256,
            "proposed_at": self.proposed_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> MndSourceProposal:
        if not isinstance(value, Mapping):
            raise ValueError("MND source proposal must be an object")
        if value.get("schema_version") != MND_SOURCE_PROPOSAL_SCHEMA_VERSION:
            raise ValueError("unsupported MND source proposal schema version")
        valid_from = _date_from_value(value.get("valid_from"), "valid_from")
        if valid_from is None:
            raise ValueError("valid_from must be an ISO-8601 date")
        proposal = cls(
            source_context_fingerprint=value.get("source_context_fingerprint"),
            product_name=value.get("product_name"),
            distributor=value.get("distributor"),
            contract_kind=value.get("contract_kind"),
            source_url=value.get("source_url"),
            valid_from=valid_from,
            valid_to=_date_from_value(value.get("valid_to"), "valid_to", optional=True),
            document_date=_date_from_value(
                value.get("document_date"), "document_date", optional=True
            ),
            document_sha256=value.get("document_sha256"),
            proposed_at=_aware_datetime(value.get("proposed_at"), "proposed_at"),
        )
        if value.get("fingerprint") != proposal.fingerprint:
            raise ValueError("MND source proposal fingerprint mismatch")
        return proposal


def mnd_source_proposals_from_options(
    options: Mapping[str, Any],
) -> tuple[MndSourceProposal, ...]:
    if not isinstance(options, Mapping):
        raise ValueError("options must be a mapping")
    raw = options.get(MND_SOURCE_PROPOSALS_OPTION, [])
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("mnd_source_proposals must be a list")
    proposals: list[MndSourceProposal] = []
    seen: set[str] = set()
    for item in raw:
        proposal = MndSourceProposal.from_dict(item)
        if proposal.fingerprint in seen:
            raise ValueError(f"duplicate MND source proposal fingerprint: {proposal.fingerprint}")
        seen.add(proposal.fingerprint)
        proposals.append(proposal)
    return tuple(proposals)


def append_mnd_source_proposal(
    options: Mapping[str, Any],
    proposal: MndSourceProposal,
) -> dict[str, Any]:
    """Append an immutable proposal; exact repeats cause no write churn."""
    if not isinstance(proposal, MndSourceProposal):
        raise ValueError("proposal must be MndSourceProposal")
    proposals = list(mnd_source_proposals_from_options(options))
    if any(item.fingerprint == proposal.fingerprint for item in proposals):
        return dict(options)
    proposals.append(proposal)
    updated = dict(options)
    updated[MND_SOURCE_PROPOSALS_OPTION] = [item.as_dict() for item in proposals]
    return updated


def confirm_mnd_source_proposal(
    options: Mapping[str, Any],
    proposal_fingerprint: str,
    *,
    confirmed_at: datetime,
) -> tuple[dict[str, Any], ConfirmedMndSourceResolution]:
    """Confirm exactly one stored proposal by fingerprint only."""
    fingerprint = _fingerprint(proposal_fingerprint)
    proposals = mnd_source_proposals_from_options(options)
    proposal = next((item for item in proposals if item.fingerprint == fingerprint), None)
    if proposal is None:
        raise LookupError(f"MND source proposal not found: {fingerprint}")

    resolution = ConfirmedMndSourceResolution(
        source_context_fingerprint=proposal.source_context_fingerprint,
        product_name=proposal.product_name,
        distributor=proposal.distributor,
        contract_kind=proposal.contract_kind,
        source_url=proposal.source_url,
        valid_from=proposal.valid_from,
        valid_to=proposal.valid_to,
        document_date=proposal.document_date,
        document_sha256=proposal.document_sha256,
        confirmed_at=_aware_datetime(confirmed_at, "confirmed_at"),
    )
    updated = append_confirmed_mnd_source_resolution(options, resolution)
    return updated, resolution


def confirmed_resolution_fingerprint_for_proposal(
    resolution: ConfirmedMndSourceResolution,
) -> str:
    return confirmed_mnd_source_resolution_fingerprint(resolution)
