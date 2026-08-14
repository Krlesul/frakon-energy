"""Conditional HTTP fetch contract for selected tariff documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re

from .tariff_candidate_selection import tariff_candidate_selection_fingerprint
from .tariff_download import (
    MAX_TARIFF_DOCUMENT_BYTES,
    ValidatedTariffDownload,
    validate_selected_tariff_download,
)
from .tariff_sources import TariffDocumentCandidate

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class TariffFetchRequest:
    """Transport-neutral request for one explicitly selected tariff candidate."""

    selected_fingerprint: str
    source_url: str
    headers: tuple[tuple[str, str], ...]
    max_bytes: int = MAX_TARIFF_DOCUMENT_BYTES
    allow_redirects: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.selected_fingerprint, str) or not _SHA256_RE.fullmatch(
            self.selected_fingerprint
        ):
            raise ValueError("selected_fingerprint must be a lowercase SHA-256 hex digest")
        if not isinstance(self.source_url, str) or not self.source_url.strip():
            raise ValueError("source_url must not be empty")
        if isinstance(self.max_bytes, bool) or not isinstance(self.max_bytes, int) or self.max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        if self.allow_redirects is not False:
            raise ValueError("tariff fetch requests must not allow redirects")
        headers = tuple(self.headers)
        if any(
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(value, str)
            or not value.strip()
            for name, value in headers
        ):
            raise ValueError("headers must contain non-empty string pairs")
        lowered = [name.strip().lower() for name, _ in headers]
        if len(set(lowered)) != len(lowered):
            raise ValueError("duplicate tariff fetch request header")
        object.__setattr__(self, "headers", headers)

    @property
    def conditional(self) -> bool:
        names = {name.lower() for name, _ in self.headers}
        return "if-none-match" in names or "if-modified-since" in names

    def headers_dict(self) -> dict[str, str]:
        return dict(self.headers)


@dataclass(frozen=True, slots=True)
class TariffHttpResponse:
    """Minimal transport-neutral HTTP response metadata used by validation."""

    status_code: int
    final_url: str
    content_type: str | None
    content: bytes
    etag: str | None = None
    last_modified: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.status_code, bool) or not isinstance(self.status_code, int):
            raise ValueError("status_code must be an integer")
        if not 100 <= self.status_code <= 599:
            raise ValueError("status_code must be a valid HTTP status")
        if not isinstance(self.final_url, str) or not self.final_url.strip():
            raise ValueError("final_url must not be empty")
        if self.content_type is not None and (
            not isinstance(self.content_type, str) or not self.content_type.strip()
        ):
            raise ValueError("content_type must be non-empty when provided")
        if not isinstance(self.content, bytes):
            raise ValueError("content must be bytes")
        for field_name in ("etag", "last_modified"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field_name} must be non-empty when provided")


@dataclass(frozen=True, slots=True)
class TariffNotModified:
    """A successful conditional revalidation that downloaded no new document body."""

    selected_fingerprint: str
    source_url: str
    checked_at: datetime
    etag: str | None
    last_modified: str | None
    changed: bool = False
    body_downloaded: bool = False
    parser_authorized: bool = False
    persistence_performed: bool = False
    activation_performed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.selected_fingerprint, str) or not _SHA256_RE.fullmatch(
            self.selected_fingerprint
        ):
            raise ValueError("selected_fingerprint must be a lowercase SHA-256 hex digest")
        if not isinstance(self.source_url, str) or not self.source_url.strip():
            raise ValueError("source_url must not be empty")
        if not isinstance(self.checked_at, datetime) or self.checked_at.tzinfo is None:
            raise ValueError("checked_at must be a timezone-aware datetime")


def build_tariff_fetch_request(
    candidate: TariffDocumentCandidate,
    *,
    selected_fingerprint: str,
    max_bytes: int = MAX_TARIFF_DOCUMENT_BYTES,
) -> TariffFetchRequest:
    """Build the only allowed HTTP request for one explicitly selected candidate."""
    if not isinstance(candidate, TariffDocumentCandidate):
        raise ValueError("candidate must be TariffDocumentCandidate")
    if not isinstance(selected_fingerprint, str) or not _SHA256_RE.fullmatch(
        selected_fingerprint
    ):
        raise ValueError("selected_fingerprint must be a lowercase SHA-256 hex digest")
    if selected_fingerprint != tariff_candidate_selection_fingerprint(candidate):
        raise ValueError("selected fingerprint does not match tariff candidate")

    headers: list[tuple[str, str]] = [("Accept", "application/pdf")]
    if candidate.document.etag is not None:
        headers.append(("If-None-Match", candidate.document.etag))
    if candidate.document.last_modified is not None:
        headers.append(("If-Modified-Since", candidate.document.last_modified))

    return TariffFetchRequest(
        selected_fingerprint=selected_fingerprint,
        source_url=candidate.document.source_url,
        headers=tuple(headers),
        max_bytes=max_bytes,
        allow_redirects=False,
    )


def process_tariff_fetch_response(
    *,
    candidate: TariffDocumentCandidate,
    request: TariffFetchRequest,
    response: TariffHttpResponse,
    checked_at: datetime,
) -> ValidatedTariffDownload | TariffNotModified:
    """Process one HTTP response without allowing silent candidate/source drift."""
    if not isinstance(candidate, TariffDocumentCandidate):
        raise ValueError("candidate must be TariffDocumentCandidate")
    if not isinstance(request, TariffFetchRequest):
        raise ValueError("request must be TariffFetchRequest")
    if not isinstance(response, TariffHttpResponse):
        raise ValueError("response must be TariffHttpResponse")
    if not isinstance(checked_at, datetime) or checked_at.tzinfo is None:
        raise ValueError("checked_at must be a timezone-aware datetime")

    expected_fingerprint = tariff_candidate_selection_fingerprint(candidate)
    if request.selected_fingerprint != expected_fingerprint:
        raise ValueError("fetch request does not match tariff candidate")
    if request.source_url != candidate.document.source_url:
        raise ValueError("fetch request source URL does not match tariff candidate")
    if response.final_url != request.source_url:
        raise ValueError("tariff fetch response URL does not match selected source")

    if response.status_code == 304:
        if not request.conditional:
            raise ValueError("HTTP 304 is invalid without a conditional tariff request")
        if response.content:
            raise ValueError("HTTP 304 tariff response must not contain a document body")
        return TariffNotModified(
            selected_fingerprint=request.selected_fingerprint,
            source_url=request.source_url,
            checked_at=checked_at,
            etag=response.etag if response.etag is not None else candidate.document.etag,
            last_modified=(
                response.last_modified
                if response.last_modified is not None
                else candidate.document.last_modified
            ),
        )

    return validate_selected_tariff_download(
        candidate=candidate,
        selected_fingerprint=request.selected_fingerprint,
        status_code=response.status_code,
        final_url=response.final_url,
        content_type=response.content_type or "",
        content=response.content,
        validated_at=checked_at,
        etag=response.etag,
        last_modified=response.last_modified,
        max_bytes=request.max_bytes,
    )
