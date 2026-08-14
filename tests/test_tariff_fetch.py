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
    return sources, selection, download, fetch


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


def _checked_at():
    return datetime(2026, 8, 14, 6, 45, tzinfo=timezone.utc)


def _pdf_bytes():
    return b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def test_build_request_uses_pdf_accept_and_available_http_validators() -> None:
    sources, selection, _, fetch = load_modules()
    candidate = _candidate(
        sources,
        etag='"v1"',
        last_modified="Fri, 14 Aug 2026 04:00:00 GMT",
    )
    fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)

    request = fetch.build_tariff_fetch_request(
        candidate,
        selected_fingerprint=fingerprint,
    )

    assert request.source_url == candidate.document.source_url
    assert request.selected_fingerprint == fingerprint
    assert request.allow_redirects is False
    assert request.conditional is True
    assert request.headers_dict() == {
        "Accept": "application/pdf",
        "If-None-Match": '"v1"',
        "If-Modified-Since": "Fri, 14 Aug 2026 04:00:00 GMT",
    }


def test_request_without_validators_is_not_conditional() -> None:
    sources, selection, _, fetch = load_modules()
    candidate = _candidate(sources)
    fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)

    request = fetch.build_tariff_fetch_request(
        candidate,
        selected_fingerprint=fingerprint,
    )

    assert request.conditional is False
    assert request.headers_dict() == {"Accept": "application/pdf"}


def test_fetch_request_requires_exact_selected_fingerprint() -> None:
    sources, _, _, fetch = load_modules()
    candidate = _candidate(sources)

    try:
        fetch.build_tariff_fetch_request(
            candidate,
            selected_fingerprint="0" * 64,
        )
    except ValueError as err:
        assert "does not match" in str(err)
    else:
        raise AssertionError("Mismatched selected fingerprint must fail closed")


def test_conditional_304_returns_no_body_no_parser_authority() -> None:
    sources, selection, _, fetch = load_modules()
    candidate = _candidate(sources, etag='"v1"')
    fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)
    request = fetch.build_tariff_fetch_request(
        candidate,
        selected_fingerprint=fingerprint,
    )
    response = fetch.TariffHttpResponse(
        status_code=304,
        final_url=candidate.document.source_url,
        content_type=None,
        content=b"",
        etag='"v1"',
    )

    result = fetch.process_tariff_fetch_response(
        candidate=candidate,
        request=request,
        response=response,
        checked_at=_checked_at(),
    )

    assert isinstance(result, fetch.TariffNotModified)
    assert result.changed is False
    assert result.body_downloaded is False
    assert result.parser_authorized is False
    assert result.persistence_performed is False
    assert result.activation_performed is False
    assert result.etag == '"v1"'


def test_304_without_conditional_request_is_rejected() -> None:
    sources, selection, _, fetch = load_modules()
    candidate = _candidate(sources)
    fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)
    request = fetch.build_tariff_fetch_request(
        candidate,
        selected_fingerprint=fingerprint,
    )
    response = fetch.TariffHttpResponse(
        status_code=304,
        final_url=candidate.document.source_url,
        content_type=None,
        content=b"",
    )

    try:
        fetch.process_tariff_fetch_response(
            candidate=candidate,
            request=request,
            response=response,
            checked_at=_checked_at(),
        )
    except ValueError as err:
        assert "conditional" in str(err)
    else:
        raise AssertionError("Unconditional HTTP 304 must fail closed")


def test_304_with_body_is_rejected() -> None:
    sources, selection, _, fetch = load_modules()
    candidate = _candidate(sources, last_modified="Fri, 14 Aug 2026 04:00:00 GMT")
    fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)
    request = fetch.build_tariff_fetch_request(
        candidate,
        selected_fingerprint=fingerprint,
    )
    response = fetch.TariffHttpResponse(
        status_code=304,
        final_url=candidate.document.source_url,
        content_type=None,
        content=b"unexpected",
    )

    try:
        fetch.process_tariff_fetch_response(
            candidate=candidate,
            request=request,
            response=response,
            checked_at=_checked_at(),
        )
    except ValueError as err:
        assert "must not contain" in str(err)
    else:
        raise AssertionError("HTTP 304 with a body must fail closed")


def test_response_url_drift_is_rejected_even_for_304() -> None:
    sources, selection, _, fetch = load_modules()
    candidate = _candidate(sources, etag='"v1"')
    fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)
    request = fetch.build_tariff_fetch_request(
        candidate,
        selected_fingerprint=fingerprint,
    )
    response = fetch.TariffHttpResponse(
        status_code=304,
        final_url="https://www.cez.cz/file/other.pdf",
        content_type=None,
        content=b"",
    )

    try:
        fetch.process_tariff_fetch_response(
            candidate=candidate,
            request=request,
            response=response,
            checked_at=_checked_at(),
        )
    except ValueError as err:
        assert "does not match" in str(err)
    else:
        raise AssertionError("Response URL drift must fail closed")


def test_http_200_delegates_to_pdf_download_validator_and_pins_content() -> None:
    sources, selection, download, fetch = load_modules()
    candidate = _candidate(sources, etag='"old"')
    fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)
    request = fetch.build_tariff_fetch_request(
        candidate,
        selected_fingerprint=fingerprint,
    )
    response = fetch.TariffHttpResponse(
        status_code=200,
        final_url=candidate.document.source_url,
        content_type="application/pdf",
        content=_pdf_bytes(),
        etag='"new"',
        last_modified="Fri, 14 Aug 2026 04:45:00 GMT",
    )

    result = fetch.process_tariff_fetch_response(
        candidate=candidate,
        request=request,
        response=response,
        checked_at=_checked_at(),
    )

    assert isinstance(result, download.ValidatedTariffDownload)
    assert result.parser_authorized is True
    assert result.document.sha256 is not None
    assert result.document.etag == '"new"'
    assert result.document.last_modified == "Fri, 14 Aug 2026 04:45:00 GMT"


def test_non_success_response_is_rejected_by_existing_download_boundary() -> None:
    sources, selection, _, fetch = load_modules()
    candidate = _candidate(sources)
    fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)
    request = fetch.build_tariff_fetch_request(
        candidate,
        selected_fingerprint=fingerprint,
    )
    response = fetch.TariffHttpResponse(
        status_code=404,
        final_url=candidate.document.source_url,
        content_type="text/html",
        content=b"not found",
    )

    try:
        fetch.process_tariff_fetch_response(
            candidate=candidate,
            request=request,
            response=response,
            checked_at=_checked_at(),
        )
    except ValueError as err:
        assert "HTTP 200" in str(err)
    else:
        raise AssertionError("Unexpected HTTP status must fail closed")
