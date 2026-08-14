"""Bounded HTTP transport executor for selected tariff documents."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from .tariff_download import ValidatedTariffDownload
from .tariff_fetch import (
    TariffFetchRequest,
    TariffHttpResponse,
    TariffNotModified,
    process_tariff_fetch_response,
)
from .tariff_sources import TariffDocumentCandidate

DEFAULT_TARIFF_HTTP_TIMEOUT_SECONDS = 20.0
TARIFF_HTTP_CHUNK_BYTES = 64 * 1024


def _header(headers: Mapping[str, Any], name: str) -> str | None:
    """Return one HTTP header case-insensitively without trusting its type."""
    if not isinstance(headers, Mapping):
        raise ValueError("HTTP response headers must be a mapping")
    target = name.casefold()
    for key, value in headers.items():
        if isinstance(key, str) and key.casefold() == target:
            if value is None:
                return None
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"HTTP response header {name} must be a non-empty string")
            return value.strip()
    return None


def _content_length(headers: Mapping[str, Any]) -> int | None:
    value = _header(headers, "Content-Length")
    if value is None:
        return None
    try:
        parsed = int(value, 10)
    except ValueError as err:
        raise ValueError("Content-Length must be a non-negative integer") from err
    if parsed < 0:
        raise ValueError("Content-Length must be a non-negative integer")
    return parsed


async def _read_bounded_body(response: Any, *, max_bytes: int) -> bytes:
    """Read a response body incrementally and abort immediately above max_bytes."""
    headers = getattr(response, "headers", None)
    declared = _content_length(headers)
    if declared is not None and declared > max_bytes:
        raise ValueError("tariff document Content-Length exceeds maximum allowed size")

    content = getattr(response, "content", None)
    iterator_factory = getattr(content, "iter_chunked", None)
    if not callable(iterator_factory):
        raise ValueError("HTTP response content must support iter_chunked")

    chunks: list[bytes] = []
    total = 0
    async for chunk in iterator_factory(TARIFF_HTTP_CHUNK_BYTES):
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise ValueError("HTTP response body chunks must be bytes")
        data = bytes(chunk)
        total += len(data)
        if total > max_bytes:
            raise ValueError("tariff document exceeds maximum allowed size while streaming")
        chunks.append(data)

    if declared is not None and total != declared:
        raise ValueError("tariff document body length does not match Content-Length")
    return b"".join(chunks)


async def async_fetch_selected_tariff_document(
    *,
    candidate: TariffDocumentCandidate,
    request: TariffFetchRequest,
    session: Any,
    checked_at: datetime,
    timeout_seconds: float = DEFAULT_TARIFF_HTTP_TIMEOUT_SECONDS,
) -> ValidatedTariffDownload | TariffNotModified:
    """Execute one exact selected-tariff GET and validate the result fail-closed.

    The session is injected so Home Assistant can supply its shared aiohttp session
    while this transport remains independently testable. Redirects are always
    disabled by the request contract. The response body is streamed under the
    request's hard byte limit before it enters the PDF/checksum validation layer.
    """
    if not isinstance(candidate, TariffDocumentCandidate):
        raise ValueError("candidate must be TariffDocumentCandidate")
    if not isinstance(request, TariffFetchRequest):
        raise ValueError("request must be TariffFetchRequest")
    if not isinstance(checked_at, datetime) or checked_at.tzinfo is None:
        raise ValueError("checked_at must be a timezone-aware datetime")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise ValueError("timeout_seconds must be a positive number")
    timeout = float(timeout_seconds)
    if timeout <= 0:
        raise ValueError("timeout_seconds must be a positive number")

    get = getattr(session, "get", None)
    if not callable(get):
        raise ValueError("session must provide a callable get method")

    response_context = get(
        request.source_url,
        headers=request.headers_dict(),
        allow_redirects=request.allow_redirects,
        timeout=timeout,
    )
    if not hasattr(response_context, "__aenter__") or not hasattr(
        response_context, "__aexit__"
    ):
        raise ValueError("session.get must return an async context manager")

    async with response_context as response:
        status = getattr(response, "status", None)
        if isinstance(status, bool) or not isinstance(status, int):
            raise ValueError("HTTP response status must be an integer")
        url = getattr(response, "url", None)
        if url is None:
            raise ValueError("HTTP response must expose its final URL")
        final_url = str(url)
        headers = getattr(response, "headers", None)
        content_type = _header(headers, "Content-Type")
        etag = _header(headers, "ETag")
        last_modified = _header(headers, "Last-Modified")
        body = await _read_bounded_body(response, max_bytes=request.max_bytes)

    http_response = TariffHttpResponse(
        status_code=status,
        final_url=final_url,
        content_type=content_type,
        content=body,
        etag=etag,
        last_modified=last_modified,
    )
    return process_tariff_fetch_response(
        candidate=candidate,
        request=request,
        response=http_response,
        checked_at=checked_at,
    )
