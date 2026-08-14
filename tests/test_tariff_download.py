from datetime import date, datetime, timezone
import hashlib
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
    return sources, selection, download


def _candidate(sources, *, sha256=None, scope=None):
    return sources.TariffDocumentCandidate(
        document=sources.OfficialTariffDocument(
            supplier="cez",
            source_url="https://www.cez.cz/file/cenik.pdf",
            discovered_at=datetime(2026, 8, 14, 6, 0, tzinfo=timezone.utc),
            document_date=date(2025, 10, 1),
            sha256=sha256,
            content_type="application/pdf",
        ),
        product_name="Basic",
        valid_from=date(2026, 1, 1),
        match_score=100,
        match_reasons=("exact product",),
        price_scope=scope or sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    )


def _pdf_bytes() -> bytes:
    return b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def test_selected_pdf_is_pinned_by_sha256_before_parser_authority() -> None:
    sources, selection, download = load_modules()
    candidate = _candidate(sources)
    fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)
    content = _pdf_bytes()
    validated_at = datetime(2026, 8, 14, 6, 30, tzinfo=timezone.utc)

    result = download.validate_selected_tariff_download(
        candidate=candidate,
        selected_fingerprint=fingerprint,
        status_code=200,
        final_url=candidate.document.source_url,
        content_type="application/pdf; charset=binary",
        content=content,
        validated_at=validated_at,
        etag='"abc"',
        last_modified="Fri, 14 Aug 2026 04:30:00 GMT",
    )

    assert result.selected_fingerprint == fingerprint
    assert result.content == content
    assert result.document.sha256 == hashlib.sha256(content).hexdigest()
    assert result.document.etag == '"abc"'
    assert result.document.last_modified == "Fri, 14 Aug 2026 04:30:00 GMT"
    assert result.document.content_type == "application/pdf"
    assert result.parser_authorized is True
    assert result.persistence_performed is False
    assert result.activation_performed is False


def test_selection_mismatch_and_noncommercial_scope_fail_closed() -> None:
    sources, selection, download = load_modules()
    candidate = _candidate(sources)
    fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)

    for bad_candidate, bad_fingerprint in (
        (candidate, "0" * 64),
        (
            _candidate(sources, scope=sources.PRICE_SCOPE_REGULATED),
            selection.tariff_candidate_selection_fingerprint(
                _candidate(sources, scope=sources.PRICE_SCOPE_REGULATED)
            ),
        ),
    ):
        try:
            download.validate_selected_tariff_download(
                candidate=bad_candidate,
                selected_fingerprint=bad_fingerprint,
                status_code=200,
                final_url=bad_candidate.document.source_url,
                content_type="application/pdf",
                content=_pdf_bytes(),
                validated_at=datetime(2026, 8, 14, 6, 30, tzinfo=timezone.utc),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Unsafe selection/scope must fail closed")

    assert fingerprint != "0" * 64


def test_redirect_status_mime_size_and_pdf_header_are_rejected() -> None:
    sources, selection, download = load_modules()
    candidate = _candidate(sources)
    fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)
    common = {
        "candidate": candidate,
        "selected_fingerprint": fingerprint,
        "status_code": 200,
        "final_url": candidate.document.source_url,
        "content_type": "application/pdf",
        "content": _pdf_bytes(),
        "validated_at": datetime(2026, 8, 14, 6, 30, tzinfo=timezone.utc),
    }

    cases = (
        {"status_code": 302},
        {"final_url": "https://www.cez.cz/file/other.pdf"},
        {"content_type": "text/html"},
        {"content": b"<html>not pdf</html>"},
        {"max_bytes": 8},
    )
    for overrides in cases:
        values = dict(common)
        values.update(overrides)
        try:
            download.validate_selected_tariff_download(**values)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Unsafe response must fail closed: {overrides}")


def test_pre_pinned_candidate_checksum_must_match_downloaded_bytes() -> None:
    sources, selection, download = load_modules()
    content = _pdf_bytes()
    correct = hashlib.sha256(content).hexdigest()
    candidate = _candidate(sources, sha256=correct)
    fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)

    result = download.validate_selected_tariff_download(
        candidate=candidate,
        selected_fingerprint=fingerprint,
        status_code=200,
        final_url=candidate.document.source_url,
        content_type="application/pdf",
        content=content,
        validated_at=datetime(2026, 8, 14, 6, 30, tzinfo=timezone.utc),
    )
    assert result.document.sha256 == correct

    mismatched = _candidate(sources, sha256="a" * 64)
    mismatch_fingerprint = selection.tariff_candidate_selection_fingerprint(mismatched)
    try:
        download.validate_selected_tariff_download(
            candidate=mismatched,
            selected_fingerprint=mismatch_fingerprint,
            status_code=200,
            final_url=mismatched.document.source_url,
            content_type="application/pdf",
            content=content,
            validated_at=datetime(2026, 8, 14, 6, 30, tzinfo=timezone.utc),
        )
    except ValueError as err:
        assert "checksum" in str(err)
    else:
        raise AssertionError("Pinned checksum mismatch must fail closed")
