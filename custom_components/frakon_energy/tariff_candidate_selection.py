"""Explicit review/selection identity for supplier tariff discovery candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import re
from typing import Any, Iterable

from .tariff_sources import TariffDocumentCandidate

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def tariff_candidate_selection_fingerprint(candidate: TariffDocumentCandidate) -> str:
    """Return stable identity for the source document/version a user selects.

    Discovery time, match score and human-readable reasons are intentionally not
    part of the identity. Adapter ranking may change and must never silently
    change which source document the user selected.
    """
    if not isinstance(candidate, TariffDocumentCandidate):
        raise ValueError("candidate must be TariffDocumentCandidate")

    payload = {
        "supplier": candidate.document.supplier,
        "source_url": candidate.document.source_url,
        "document_sha256": candidate.document.sha256,
        "document_date": (
            candidate.document.document_date.isoformat()
            if candidate.document.document_date is not None
            else None
        ),
        "product_name": candidate.product_name,
        "valid_from": candidate.valid_from.isoformat(),
        "valid_to": candidate.valid_to.isoformat() if candidate.valid_to else None,
        "price_scope": candidate.price_scope,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class TariffCandidateReviewItem:
    """UI-safe read-only summary; it carries no pricing or activation authority."""

    fingerprint: str
    supplier: str
    product_name: str
    source_url: str
    valid_from: date
    valid_to: date | None
    match_score: int
    match_reasons: tuple[str, ...]
    price_scope: str
    document_sha256: str | None
    document_date: date | None
    download_performed: bool = False
    parsing_performed: bool = False
    persistence_performed: bool = False
    activation_performed: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Serialize the review record to Home Assistant websocket-safe values."""
        return {
            "fingerprint": self.fingerprint,
            "supplier": self.supplier,
            "product_name": self.product_name,
            "source_url": self.source_url,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to is not None else None,
            "match_score": self.match_score,
            "match_reasons": list(self.match_reasons),
            "price_scope": self.price_scope,
            "document_sha256": self.document_sha256,
            "document_date": (
                self.document_date.isoformat()
                if self.document_date is not None
                else None
            ),
            "download_performed": self.download_performed,
            "parsing_performed": self.parsing_performed,
            "persistence_performed": self.persistence_performed,
            "activation_performed": self.activation_performed,
        }


def candidate_review_items(
    candidates: Iterable[TariffDocumentCandidate],
) -> tuple[TariffCandidateReviewItem, ...]:
    """Create review items and reject duplicate immutable selection identities."""
    items: list[TariffCandidateReviewItem] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, TariffDocumentCandidate):
            raise ValueError("candidates must contain TariffDocumentCandidate records")
        fingerprint = tariff_candidate_selection_fingerprint(candidate)
        if fingerprint in seen:
            raise ValueError(f"duplicate tariff candidate identity: {fingerprint}")
        seen.add(fingerprint)
        items.append(
            TariffCandidateReviewItem(
                fingerprint=fingerprint,
                supplier=candidate.document.supplier,
                product_name=candidate.product_name,
                source_url=candidate.document.source_url,
                valid_from=candidate.valid_from,
                valid_to=candidate.valid_to,
                match_score=candidate.match_score,
                match_reasons=candidate.match_reasons,
                price_scope=candidate.price_scope,
                document_sha256=candidate.document.sha256,
                document_date=candidate.document.document_date,
            )
        )
    return tuple(items)


def select_tariff_candidate(
    candidates: Iterable[TariffDocumentCandidate],
    *,
    fingerprint: str,
) -> TariffDocumentCandidate:
    """Select exactly one current candidate by explicit stable fingerprint."""
    if not isinstance(fingerprint, str) or not _SHA256_RE.fullmatch(fingerprint):
        raise ValueError("fingerprint must be a lowercase SHA-256 hex digest")

    selected: TariffDocumentCandidate | None = None
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, TariffDocumentCandidate):
            raise ValueError("candidates must contain TariffDocumentCandidate records")
        current = tariff_candidate_selection_fingerprint(candidate)
        if current in seen:
            raise ValueError(f"duplicate tariff candidate identity: {current}")
        seen.add(current)
        if current == fingerprint:
            selected = candidate

    if selected is None:
        raise LookupError(f"tariff candidate not found: {fingerprint}")
    return selected
