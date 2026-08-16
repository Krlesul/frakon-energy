import asyncio
from datetime import date, datetime, timezone
import importlib.util
from pathlib import Path
import sys
import types


def load_modules():
    for name in (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components.frakon_energy.tariff_download",
        "custom_components.frakon_energy.tariff_fetch",
        "custom_components.frakon_energy.tariff_http_transport",
    ):
        sys.modules.pop(name, None)

    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    def load(name: str, path: str):
        spec = importlib.util.spec_from_file_location(name, Path(path))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    sources = load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    selection = load(
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components/frakon_energy/tariff_candidate_selection.py",
    )
    download = load(
        "custom_components.frakon_energy.tariff_download",
        "custom_components/frakon_energy/tariff_download.py",
    )
    fetch = load(
        "custom_components.frakon_energy.tariff_fetch",
        "custom_components/frakon_energy/tariff_fetch.py",
    )
    transport = load(
        "custom_components.frakon_energy.tariff_http_transport",
        "custom_components/frakon_energy/tariff_http_transport.py",
    )
    return sources, selection, download, fetch, transport


class FakeContent:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.iterated = False
        self.chunk_size = None

    def iter_chunked(self, chunk_size):
        self.chunk_size = chunk_size

        async def iterator():
            self.iterated = True
            for chunk in self.chunks:
                yield chunk

        return iterator()


class FakeResponse:
    def __init__(self, *, status, url, headers, chunks):
        self.status = status
        self.url = url
        self.headers = headers
        self.content = FakeContent(chunks)
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True
        return False


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def _candidate(sources, *, etag=None, last_modified=None):
    return sources.TariffDocumentCandidate(
        document=sources.OfficialTariffDocument(
            supplier="cez",
            source_url="https://www.cez.cz/file/cenik.pdf",
            discovered_at=datetime(2026, 8, 14, 6, 0, tzinfo=timezone.utc),
            document_date=date(2025, 10, 1),
            etag=etag,
            last_modified=last_modified,
            content_type="application/pdf",
        ),
        product_name="Basic",
        valid_from=date(2026, 1, 1),
        match_score=100,
        match_reasons=("exact product",),
        price_scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    )


def _request(candidate, selection, fetch, *, max_bytes=None):
    fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)
    kwargs = {}
    if max_bytes is not None:
        kwargs["max_bytes"] = max_bytes
    request = fetch.build_tariff_fetch_request(
        candidate,
        selected_fingerprint=fingerprint,
        **kwargs,
    )
    return fingerprint, request


def _pdf_bytes():
    return b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def _checked_at():
    return datetime(2026, 8, 14, 7, 0, tzinfo=timezone.utc)


def test_transport_executes_exact_bounded_get_and_returns_validated_download() -> None:
    sources, selection, download, fetch, transport = load_modules()
    candidate = _candidate(sources, etag='"v1"')
    fingerprint, request = _request(candidate, selection, fetch)
    content = _pdf_bytes()
    response = FakeResponse(
        status=200,
        url=candidate.document.source_url,
        headers={
            "Content-Type": "application/pdf",
            "Content-Length": str(len(content)),
            "ETag": '"v2"',
            "Last-Modified": "Fri, 14 Aug 2026 05:00:00 GMT",
        },
        chunks=(content[:10], content[10:]),
    )
    session = FakeSession(response)

    result = asyncio.run(
        transport.async_fetch_selected_tariff_document(
            candidate=candidate,
            request=request,
            session=session,
            checked_at=_checked_at(),
            timeout_seconds=12,
        )
    )

    assert isinstance(result, download.ValidatedTariffDownload)
    assert result.selected_fingerprint == fingerprint
    assert result.content == content
    assert result.document.etag == '"v2"'
    assert response.entered is True
    assert response.exited is True
    assert response.content.iterated is True
    assert response.content.chunk_size == transport.TARIFF_HTTP_CHUNK_BYTES
    assert session.calls == [
        (
            candidate.document.source_url,
            {
                "headers": request.headers_dict(),
                "allow_redirects": False,
                "timeout": 12.0,
                "max_line_size": transport.TARIFF_HTTP_MAX_LINE_SIZE,
                "max_field_size": transport.TARIFF_HTTP_MAX_FIELD_SIZE,
            },
        )
    ]


def test_content_length_over_limit_fails_before_streaming_body() -> None:
    sources, selection, _, fetch, transport = load_modules()
    candidate = _candidate(sources)
    _, request = _request(candidate, selection, fetch, max_bytes=32)
    response = FakeResponse(
        status=200,
        url=candidate.document.source_url,
        headers={"Content-Type": "application/pdf", "Content-Length": "100"},
        chunks=(_pdf_bytes(),),
    )
    session = FakeSession(response)

    try:
        asyncio.run(
            transport.async_fetch_selected_tariff_document(
                candidate=candidate,
                request=request,
                session=session,
                checked_at=_checked_at(),
            )
        )
    except ValueError as err:
        assert "Content-Length exceeds" in str(err)
    else:
        raise AssertionError("Oversized declared body must fail before streaming")

    assert response.content.iterated is False


def test_streaming_body_aborts_immediately_when_cumulative_limit_is_exceeded() -> None:
    sources, selection, _, fetch, transport = load_modules()
    candidate = _candidate(sources)
    _, request = _request(candidate, selection, fetch, max_bytes=20)
    response = FakeResponse(
        status=200,
        url=candidate.document.source_url,
        headers={"Content-Type": "application/pdf"},
        chunks=(b"%PDF-1.7\n", b"01234567890123456789"),
    )
    session = FakeSession(response)

    try:
        asyncio.run(
            transport.async_fetch_selected_tariff_document(
                candidate=candidate,
                request=request,
                session=session,
                checked_at=_checked_at(),
            )
        )
    except ValueError as err:
        assert "while streaming" in str(err)
    else:
        raise AssertionError("Streaming overflow must fail closed")


def test_declared_content_length_must_equal_actual_streamed_length() -> None:
    sources, selection, _, fetch, transport = load_modules()
    candidate = _candidate(sources)
    _, request = _request(candidate, selection, fetch)
    content = _pdf_bytes()
    response = FakeResponse(
        status=200,
        url=candidate.document.source_url,
        headers={
            "Content-Type": "application/pdf",
            "Content-Length": str(len(content) + 1),
        },
        chunks=(content,),
    )

    try:
        asyncio.run(
            transport.async_fetch_selected_tariff_document(
                candidate=candidate,
                request=request,
                session=FakeSession(response),
                checked_at=_checked_at(),
            )
        )
    except ValueError as err:
        assert "does not match Content-Length" in str(err)
    else:
        raise AssertionError("Truncated response must fail closed")


def test_conditional_304_uses_same_transport_without_parser_authority() -> None:
    sources, selection, _, fetch, transport = load_modules()
    candidate = _candidate(sources, etag='"v1"')
    _, request = _request(candidate, selection, fetch)
    response = FakeResponse(
        status=304,
        url=candidate.document.source_url,
        headers={"ETag": '"v1"', "Content-Length": "0"},
        chunks=(),
    )

    result = asyncio.run(
        transport.async_fetch_selected_tariff_document(
            candidate=candidate,
            request=request,
            session=FakeSession(response),
            checked_at=_checked_at(),
        )
    )

    assert isinstance(result, fetch.TariffNotModified)
    assert result.changed is False
    assert result.body_downloaded is False
    assert result.parser_authorized is False


def test_transport_final_url_drift_is_rejected_by_fetch_contract() -> None:
    sources, selection, _, fetch, transport = load_modules()
    candidate = _candidate(sources, etag='"v1"')
    _, request = _request(candidate, selection, fetch)
    response = FakeResponse(
        status=304,
        url="https://www.cez.cz/file/redirected.pdf",
        headers={"ETag": '"v1"', "Content-Length": "0"},
        chunks=(),
    )

    try:
        asyncio.run(
            transport.async_fetch_selected_tariff_document(
                candidate=candidate,
                request=request,
                session=FakeSession(response),
                checked_at=_checked_at(),
            )
        )
    except ValueError as err:
        assert "does not match selected source" in str(err)
    else:
        raise AssertionError("Final URL drift must fail closed")


def test_invalid_content_length_and_transport_contract_are_rejected() -> None:
    sources, selection, _, fetch, transport = load_modules()
    candidate = _candidate(sources)
    _, request = _request(candidate, selection, fetch)

    for content_length in ("invalid", "-1"):
        response = FakeResponse(
            status=200,
            url=candidate.document.source_url,
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": content_length,
            },
            chunks=(_pdf_bytes(),),
        )
        try:
            asyncio.run(
                transport.async_fetch_selected_tariff_document(
                    candidate=candidate,
                    request=request,
                    session=FakeSession(response),
                    checked_at=_checked_at(),
                )
            )
        except ValueError as err:
            assert "Content-Length" in str(err)
        else:
            raise AssertionError("Invalid Content-Length must fail closed")

    try:
        asyncio.run(
            transport.async_fetch_selected_tariff_document(
                candidate=candidate,
                request=request,
                session=object(),
                checked_at=_checked_at(),
            )
        )
    except ValueError as err:
        assert "callable get" in str(err)
    else:
        raise AssertionError("Invalid session must fail closed")
