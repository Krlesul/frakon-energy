"""Conditional fetch pipeline for confirmed tariff source watches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

from .tariff_download import MAX_TARIFF_DOCUMENT_BYTES, validate_tariff_pdf_response
from .tariff_fetch import TariffHttpResponse
from .tariff_http_transport import (
    DEFAULT_TARIFF_HTTP_TIMEOUT_SECONDS,
    async_execute_bounded_tariff_get,
)
from .tariff_source_watch import (
    STATUS_CHANGE_DETECTED,
    TariffSourceCheckResult,
    TariffSourceWatch,
    evaluate_tariff_source_download,
    tariff_source_not_modified,
    tariff_source_watch_fingerprint,
)
from .tariff_sources import OfficialTariffDocument

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class TariffSourceWatchFetchRequest:
    """Conditional GET authority derived from one confirmed source-watch target."""

    watch_fingerprint: str
    source_url: str
    headers: tuple[tuple[str, str], ...]
    max_bytes: int = MAX_TARIFF_DOCUMENT_BYTES
    allow_redirects: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.watch_fingerprint, str) or not _SHA256_RE.fullmatch(
            self.watch_fingerprint
        ):
            raise ValueError("watch_fingerprint must be a lowercase SHA-256 hex digest")
        if not isinstance(self.source_url, str) or not self.source_url.strip():
            raise ValueError("source_url must not be empty")
        if isinstance(self.max_bytes, bool) or not isinstance(self.max_bytes, int) or self.max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        if self.allow_redirects is not False:
            raise ValueError("tariff source watch fetch must not allow redirects")
        headers = tuple(self.headers)
        seen: set[str] = set()
        for name, value in headers:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("watch request header names must be non-empty strings")
            if not isinstance(value, str) or not value.strip():
                raise ValueError("watch request header values must be non-empty strings")
            folded = name.strip().casefold()
            if folded in seen:
                raise ValueError("duplicate tariff source watch request header")
            seen.add(folded)
        object.__setattr__(self, "headers", headers)

    @property
    def conditional(self) -> bool:
        names = {name.casefold() for name, _ in self.headers}
        return "if-none-match" in names or "if-modified-since" in names

    def headers_dict(self) -> dict[str, str]:
        return dict(self.headers)


@dataclass(frozen=True, slots=True)
class TariffSourceWatchFetchOutcome:
    """One source-watch observation with no direct persistence/activation rights."""

    check: TariffSourceCheckResult
    observed_document: OfficialTariffDocument | None = None
    content: bytes | None = None
    parser_authorized: bool = False
    persistence_performed: bool = False
    activation_performed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.check, TariffSourceCheckResult):
            raise ValueError("check must be TariffSourceCheckResult")
        if self.persistence_performed is not False or self.activation_performed is not False:
            raise ValueError("source-watch fetch outcome cannot persist or activate prices")
        changed = self.check.status == STATUS_CHANGE_DETECTED
        if changed:
            if not isinstance(self.observed_document, OfficialTariffDocument):
                raise ValueError("changed source-watch outcome requires observed document")
            if self.observed_document.sha256 != self.check.observed_sha256:
                raise ValueError("observed document checksum must match source check")
            if not isinstance(self.content, bytes) or not self.content:
                raise ValueError("changed source-watch outcome requires PDF content")
            if self.parser_authorized is not True:
                raise ValueError("changed source-watch outcome must authorize parser review")
        else:
            if self.observed_document is not None or self.content is not None:
                raise ValueError("unchanged source-watch outcome must discard document body")
            if self.parser_authorized:
                raise ValueError("unchanged source-watch outcome cannot authorize parser")


def build_tariff_source_watch_fetch_request(
    watch: TariffSourceWatch,
    *,
    max_bytes: int = MAX_TARIFF_DOCUMENT_BYTES,
) -> TariffSourceWatchFetchRequest:
    """Build a conditional request from durable validators on a confirmed watch."""
    if not isinstance(watch, TariffSourceWatch):
        raise ValueError("watch must be TariffSourceWatch")
    headers: list[tuple[str, str]] = [("Accept", "application/pdf")]
    if watch.etag is not None:
        headers.append(("If-None-Match", watch.etag))
    if watch.last_modified is not None:
        headers.append(("If-Modified-Since", watch.last_modified))
    return TariffSourceWatchFetchRequest(
        watch_fingerprint=tariff_source_watch_fingerprint(watch),
        source_url=watch.source_url,
        headers=tuple(headers),
        max_bytes=max_bytes,
        allow_redirects=False,
    )


def process_tariff_source_watch_response(
    *,
    watch: TariffSourceWatch,
    request: TariffSourceWatchFetchRequest,
    response: TariffHttpResponse,
    checked_at: datetime,
) -> TariffSourceWatchFetchOutcome:
    """Classify one watch response without modifying confirmed pricing authority."""
    if not isinstance(watch, TariffSourceWatch):
        raise ValueError("watch must be TariffSourceWatch")
    if not isinstance(request, TariffSourceWatchFetchRequest):
        raise ValueError("request must be TariffSourceWatchFetchRequest")
    if not isinstance(response, TariffHttpResponse):
        raise ValueError("response must be TariffHttpResponse")
    if not isinstance(checked_at, datetime) or checked_at.tzinfo is None:
        raise ValueError("checked_at must be a timezone-aware datetime")
    fingerprint = tariff_source_watch_fingerprint(watch)
    if request.watch_fingerprint != fingerprint:
        raise ValueError("watch fetch request does not match tariff source watch")
    if request.source_url != watch.source_url:
        raise ValueError("watch fetch request URL does not match tariff source watch")
    if response.final_url != watch.source_url:
        raise ValueError("watch fetch response URL does not match confirmed source")

    if response.status_code == 304:
        if not request.conditional:
            raise ValueError("HTTP 304 is invalid without conditional source-watch request")
        if response.content:
            raise ValueError("HTTP 304 source-watch response must not contain a body")
        check = tariff_source_not_modified(
            watch,
            checked_at=checked_at,
            etag=response.etag,
            last_modified=response.last_modified,
        )
        return TariffSourceWatchFetchOutcome(check=check)

    validated = validate_tariff_pdf_response(
        expected_url=watch.source_url,
        status_code=response.status_code,
        final_url=response.final_url,
        content_type=response.content_type or "",
        content=response.content,
        validated_at=checked_at,
        etag=response.etag,
        last_modified=response.last_modified,
        max_bytes=request.max_bytes,
        expected_sha256=None,
    )
    observed = OfficialTariffDocument(
        supplier=watch.supplier,
        source_url=watch.source_url,
        discovered_at=checked_at,
        document_date=None,
        sha256=validated.sha256,
        etag=validated.etag,
        last_modified=validated.last_modified,
        content_type=validated.content_type,
    )
    check = evaluate_tariff_source_download(
        watch,
        document=observed,
        checked_at=checked_at,
    )
    if check.status != STATUS_CHANGE_DETECTED:
        return TariffSourceWatchFetchOutcome(check=check)
    return TariffSourceWatchFetchOutcome(
        check=check,
        observed_document=observed,
        content=validated.content,
        parser_authorized=True,
    )


async def async_fetch_tariff_source_watch(
    *,
    watch: TariffSourceWatch,
    request: TariffSourceWatchFetchRequest,
    session: Any,
    checked_at: datetime,
    timeout_seconds: float = DEFAULT_TARIFF_HTTP_TIMEOUT_SECONDS,
) -> TariffSourceWatchFetchOutcome:
    """Execute one bounded conditional source-watch GET and classify its result."""
    if not isinstance(watch, TariffSourceWatch):
        raise ValueError("watch must be TariffSourceWatch")
    if not isinstance(request, TariffSourceWatchFetchRequest):
        raise ValueError("request must be TariffSourceWatchFetchRequest")
    response = await async_execute_bounded_tariff_get(
        source_url=request.source_url,
        headers=request.headers_dict(),
        allow_redirects=request.allow_redirects,
        max_bytes=request.max_bytes,
        session=session,
        timeout_seconds=timeout_seconds,
    )
    return process_tariff_source_watch_response(
        watch=watch,
        request=request,
        response=response,
        checked_at=checked_at,
    )
