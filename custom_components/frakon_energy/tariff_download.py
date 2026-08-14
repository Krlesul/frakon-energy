"""Fail-closed validation boundary for explicitly selected tariff documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import re

from .tariff_candidate_selection import tariff_candidate_selection_fingerprint
from .tariff_sources import (
    PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    OfficialTariffDocument,
    TariffDocumentCandidate,
)

MAX_TARIFF_DOCUMENT_BYTES = 20 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ValidatedTariffDownload:
    """Downloaded PDF pinned to the exact candidate explicitly selected by the user."""

    selected_fingerprint: str
    candidate: TariffDocumentCandidate
    document: OfficialTariffDocument
    content: bytes
    validated_at: datetime
    parser_authorized: bool = True
    persistence_performed: bool = False
    activation_performed: bool = False

    def __post_init__(self) -> None:
        if not _SHA256_RE.fullmatch(self.selected_fingerprint):
            raise ValueError("selected_fingerprint must be a lowercase SHA-256 hex digest")
        if not isinstance(self.candidate, TariffDocumentCandidate):
            raise ValueError("candidate must be TariffDocumentCandidate")
        if not isinstance(self.document, OfficialTariffDocument):
            raise ValueError("document must be OfficialTariffDocument")
        if not isinstance(self.content, bytes) or not self.content:
            raise ValueError("content must be non-empty bytes")
        if not isinstance(self.validated_at, datetime) or self.validated_at.tzinfo is None:
            raise ValueError("validated_at must be a timezone-aware datetime")
        if self.document.sha256 != hashlib.sha256(self.content).hexdigest():
            raise ValueError("document sha256 must match content")


def _normalized_content_type(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("content_type must not be empty")
    return value.split(";", 1)[0].strip().lower()


def validate_selected_tariff_download(
    *,
    candidate: TariffDocumentCandidate,
    selected_fingerprint: str,
    status_code: int,
    final_url: str,
    content_type: str,
    content: bytes,
    validated_at: datetime,
    etag: str | None = None,
    last_modified: str | None = None,
    max_bytes: int = MAX_TARIFF_DOCUMENT_BYTES,
) -> ValidatedTariffDownload:
    """Validate one downloaded PDF before any tariff parser may consume it.

    The HTTP transport is intentionally outside this pure boundary. Callers must
    provide the response metadata and bytes obtained for the exact candidate the
    user selected. Redirects, content-type drift, oversized payloads and checksum
    mismatches are rejected before parsing.
    """
    if not isinstance(candidate, TariffDocumentCandidate):
        raise ValueError("candidate must be TariffDocumentCandidate")
    if not isinstance(selected_fingerprint, str) or not _SHA256_RE.fullmatch(
        selected_fingerprint
    ):
        raise ValueError("selected_fingerprint must be a lowercase SHA-256 hex digest")
    expected_fingerprint = tariff_candidate_selection_fingerprint(candidate)
    if selected_fingerprint != expected_fingerprint:
        raise ValueError("selected fingerprint does not match tariff candidate")
    if candidate.price_scope != PRICE_SCOPE_SUPPLIER_COMMERCIAL:
        raise ValueError("download boundary accepts only supplier-commercial candidates")
    if isinstance(status_code, bool) or status_code != 200:
        raise ValueError("tariff document download must return HTTP 200")
    if not isinstance(final_url, str) or final_url != candidate.document.source_url:
        raise ValueError("tariff document redirect or URL mismatch is not allowed")
    if _normalized_content_type(content_type) != "application/pdf":
        raise ValueError("tariff document content type must be application/pdf")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    if not isinstance(content, bytes) or not content:
        raise ValueError("tariff document content must be non-empty bytes")
    if len(content) > max_bytes:
        raise ValueError("tariff document exceeds maximum allowed size")
    if b"%PDF-" not in content[:1024]:
        raise ValueError("tariff document does not contain a PDF header")
    if not isinstance(validated_at, datetime) or validated_at.tzinfo is None:
        raise ValueError("validated_at must be a timezone-aware datetime")

    digest = hashlib.sha256(content).hexdigest()
    pinned = candidate.document.sha256
    if pinned is not None and digest != pinned:
        raise ValueError("tariff document checksum does not match selected candidate")

    document = OfficialTariffDocument(
        supplier=candidate.document.supplier,
        source_url=candidate.document.source_url,
        discovered_at=candidate.document.discovered_at,
        document_date=candidate.document.document_date,
        sha256=digest,
        etag=etag if etag is not None else candidate.document.etag,
        last_modified=(
            last_modified
            if last_modified is not None
            else candidate.document.last_modified
        ),
        content_type="application/pdf",
    )
    return ValidatedTariffDownload(
        selected_fingerprint=selected_fingerprint,
        candidate=candidate,
        document=document,
        content=content,
        validated_at=validated_at,
    )
