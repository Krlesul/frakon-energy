import asyncio
from datetime import date, datetime, timezone
import hashlib
import importlib.util
from pathlib import Path
import sys
import types


def load_modules():
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.pricing",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components.frakon_energy.tariff_download",
        "custom_components.frakon_energy.tariff_fetch",
        "custom_components.frakon_energy.tariff_http_transport",
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components.frakon_energy.tariff_assembly",
        "custom_components.frakon_energy.all_in_catalog",
        "custom_components.frakon_energy.tariff_source_watch",
        "custom_components.frakon_energy.tariff_source_watch_fetch",
    )
    for name in names:
        sys.modules.pop(name, None)

    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, Path(path))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    load(
        "custom_components.frakon_energy.pricing",
        "custom_components/frakon_energy/pricing.py",
    )
    sources = load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    load(
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
    load(
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components/frakon_energy/tariff_provenance.py",
    )
    load(
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components/frakon_energy/regulated_pricing.py",
    )
    load(
        "custom_components.frakon_energy.tariff_assembly",
        "custom_components/frakon_energy/tariff_assembly.py",
    )
    load(
        "custom_components.frakon_energy.all_in_catalog",
        "custom_components/frakon_energy/all_in_catalog.py",
    )
    watch = load(
        "custom_components.frakon_energy.tariff_source_watch",
        "custom_components/frakon_energy/tariff_source_watch.py",
    )
    watch_fetch = load(
        "custom_components.frakon_energy.tariff_source_watch_fetch",
        "custom_components/frakon_energy/tariff_source_watch_fetch.py",
    )
    return sources, download, fetch, transport, watch, watch_fetch


def _pdf(label=b"v2"):
    return b"%PDF-1.7\n" + label + b"\n%%EOF\n"


def _watch(watch, *, active_sha256=None, etag='"v1"', last_modified=None):
    if active_sha256 is None:
        active_sha256 = "a" * 64
    return watch.TariffSourceWatch(
        supplier="cez",
        product_name="Basic",
        source_name="ČEZ Prodej",
        document_name="Basic 2026",
        source_url="https://www.cez.cz/file/edee/basic-2026.pdf",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        active_sha256=active_sha256,
        document_date=date(2025, 10, 1),
        etag=etag,
        last_modified=last_modified,
    )


def _checked_at():
    return datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)


class FakeContent:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def iter_chunked(self, _chunk_size):
        async def iterator():
            for chunk in self.chunks:
                yield chunk
        return iterator()


class FakeResponse:
    def __init__(self, *, status, url, headers, chunks):
        self.status = status
        self.url = url
        self.headers = headers
        self.content = FakeContent(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_watch_request_uses_durable_validators_and_never_allows_redirects() -> None:
    _, _, _, _, watch, watch_fetch = load_modules()
    source_watch = _watch(
        watch,
        etag='"v1"',
        last_modified="Thu, 01 Jan 2026 00:00:00 GMT",
    )

    request = watch_fetch.build_tariff_source_watch_fetch_request(source_watch)

    assert request.watch_fingerprint == watch.tariff_source_watch_fingerprint(source_watch)
    assert request.source_url == source_watch.source_url
    assert request.allow_redirects is False
    assert request.conditional is True
    assert request.headers_dict() == {
        "Accept": "application/pdf",
        "If-None-Match": '"v1"',
        "If-Modified-Since": "Thu, 01 Jan 2026 00:00:00 GMT",
    }


def test_conditional_304_produces_no_parser_or_activation_authority() -> None:
    _, _, fetch, _, watch, watch_fetch = load_modules()
    source_watch = _watch(watch, etag='"v1"')
    request = watch_fetch.build_tariff_source_watch_fetch_request(source_watch)
    response = fetch.TariffHttpResponse(
        status_code=304,
        final_url=source_watch.source_url,
        content_type=None,
        content=b"",
        etag='"v1"',
    )

    outcome = watch_fetch.process_tariff_source_watch_response(
        watch=source_watch,
        request=request,
        response=response,
        checked_at=_checked_at(),
    )

    assert outcome.check.status == watch.STATUS_NOT_MODIFIED
    assert outcome.check.active_sha256 == source_watch.active_sha256
    assert outcome.observed_document is None
    assert outcome.content is None
    assert outcome.parser_authorized is False
    assert outcome.persistence_performed is False
    assert outcome.activation_performed is False


def test_unconditional_304_and_url_drift_fail_closed() -> None:
    _, _, fetch, _, watch, watch_fetch = load_modules()
    source_watch = _watch(watch, etag=None)
    request = watch_fetch.build_tariff_source_watch_fetch_request(source_watch)
    assert request.conditional is False

    for response in (
        fetch.TariffHttpResponse(
            status_code=304,
            final_url=source_watch.source_url,
            content_type=None,
            content=b"",
        ),
        fetch.TariffHttpResponse(
            status_code=200,
            final_url="https://www.cez.cz/file/edee/other.pdf",
            content_type="application/pdf",
            content=_pdf(),
        ),
    ):
        try:
            watch_fetch.process_tariff_source_watch_response(
                watch=source_watch,
                request=request,
                response=response,
                checked_at=_checked_at(),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Unsafe source-watch response must fail closed")


def test_same_downloaded_hash_is_discarded_without_parser_authority() -> None:
    _, _, fetch, _, watch, watch_fetch = load_modules()
    content = _pdf(b"same")
    digest = hashlib.sha256(content).hexdigest()
    source_watch = _watch(watch, active_sha256=digest, etag=None)
    request = watch_fetch.build_tariff_source_watch_fetch_request(source_watch)
    response = fetch.TariffHttpResponse(
        status_code=200,
        final_url=source_watch.source_url,
        content_type="application/pdf",
        content=content,
        etag='"same"',
    )

    outcome = watch_fetch.process_tariff_source_watch_response(
        watch=source_watch,
        request=request,
        response=response,
        checked_at=_checked_at(),
    )

    assert outcome.check.status == watch.STATUS_UNCHANGED_HASH
    assert outcome.check.observed_sha256 == digest
    assert outcome.observed_document is None
    assert outcome.content is None
    assert outcome.parser_authorized is False


def test_changed_downloaded_hash_becomes_parser_review_proposal_only() -> None:
    _, _, fetch, _, watch, watch_fetch = load_modules()
    content = _pdf(b"new official content")
    digest = hashlib.sha256(content).hexdigest()
    source_watch = _watch(watch, active_sha256="a" * 64, etag='"v1"')
    request = watch_fetch.build_tariff_source_watch_fetch_request(source_watch)
    response = fetch.TariffHttpResponse(
        status_code=200,
        final_url=source_watch.source_url,
        content_type="application/pdf; charset=binary",
        content=content,
        etag='"v2"',
        last_modified="Fri, 14 Aug 2026 06:00:00 GMT",
    )

    outcome = watch_fetch.process_tariff_source_watch_response(
        watch=source_watch,
        request=request,
        response=response,
        checked_at=_checked_at(),
    )

    assert outcome.check.status == watch.STATUS_CHANGE_DETECTED
    assert outcome.check.active_sha256 == "a" * 64
    assert outcome.check.observed_sha256 == digest
    assert outcome.check.requires_confirmation is True
    assert outcome.observed_document.sha256 == digest
    assert outcome.observed_document.document_date is None
    assert outcome.content == content
    assert outcome.parser_authorized is True
    assert outcome.persistence_performed is False
    assert outcome.activation_performed is False


def test_changed_body_still_uses_shared_pdf_mime_size_and_magic_validation() -> None:
    _, _, fetch, _, watch, watch_fetch = load_modules()
    source_watch = _watch(watch, etag=None)
    request = watch_fetch.build_tariff_source_watch_fetch_request(
        source_watch,
        max_bytes=32,
    )

    bad_responses = (
        fetch.TariffHttpResponse(
            status_code=200,
            final_url=source_watch.source_url,
            content_type="text/html",
            content=b"<html>not pdf</html>",
        ),
        fetch.TariffHttpResponse(
            status_code=200,
            final_url=source_watch.source_url,
            content_type="application/pdf",
            content=b"not a pdf",
        ),
        fetch.TariffHttpResponse(
            status_code=200,
            final_url=source_watch.source_url,
            content_type="application/pdf",
            content=b"%PDF-1.7\n" + b"x" * 64,
        ),
    )
    for response in bad_responses:
        try:
            watch_fetch.process_tariff_source_watch_response(
                watch=source_watch,
                request=request,
                response=response,
                checked_at=_checked_at(),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid watch PDF response must fail closed")


def test_async_watch_fetch_reuses_bounded_transport_end_to_end() -> None:
    _, _, _, transport, watch, watch_fetch = load_modules()
    content = _pdf(b"new bounded content")
    source_watch = _watch(watch, active_sha256="a" * 64, etag='"v1"')
    request = watch_fetch.build_tariff_source_watch_fetch_request(source_watch)
    response = FakeResponse(
        status=200,
        url=source_watch.source_url,
        headers={
            "Content-Type": "application/pdf",
            "Content-Length": str(len(content)),
            "ETag": '"v2"',
        },
        chunks=(content[:8], content[8:]),
    )
    session = FakeSession(response)

    outcome = asyncio.run(
        watch_fetch.async_fetch_tariff_source_watch(
            watch=source_watch,
            request=request,
            session=session,
            checked_at=_checked_at(),
            timeout_seconds=9,
        )
    )

    assert outcome.check.status == watch.STATUS_CHANGE_DETECTED
    assert outcome.parser_authorized is True
    assert session.calls == [
        (
            source_watch.source_url,
            {
                "headers": request.headers_dict(),
                "allow_redirects": False,
                "timeout": 9.0,
                "max_line_size": transport.TARIFF_HTTP_MAX_LINE_SIZE,
                "max_field_size": transport.TARIFF_HTTP_MAX_FIELD_SIZE,
            },
        )
    ]
    assert transport.TARIFF_HTTP_CHUNK_BYTES == 64 * 1024
