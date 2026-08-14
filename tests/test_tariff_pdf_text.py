from datetime import date, datetime, timezone
import hashlib
import importlib.util
from io import BytesIO
from pathlib import Path
import sys
import types

import pytest
from pypdf import PdfWriter


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_modules():
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components.frakon_energy.tariff_download",
        "custom_components.frakon_energy.tariff_pdf_text",
    )
    for name in names:
        sys.modules.pop(name, None)

    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    sources = _load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    selection = _load(
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components/frakon_energy/tariff_candidate_selection.py",
    )
    download = _load(
        "custom_components.frakon_energy.tariff_download",
        "custom_components/frakon_energy/tariff_download.py",
    )
    extractor = _load(
        "custom_components.frakon_energy.tariff_pdf_text",
        "custom_components/frakon_energy/tariff_pdf_text.py",
    )
    return sources, selection, download, extractor


VALIDATED_AT = datetime(2026, 8, 14, 17, 0, tzinfo=timezone.utc)
SOURCE_URL = "https://www.cez.cz/file/verified.pdf"


def _minimal_text_pdf(text: str) -> bytes:
    """Build one deterministic, uncompressed PDF with a real text layer."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    )
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


def _writer_pdf(*, pages: int = 1, encrypted: bool = False) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    if encrypted:
        writer.encrypt("secret")
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _validated_download(sources, selection, download, content: bytes):
    candidate = sources.TariffDocumentCandidate(
        document=sources.OfficialTariffDocument(
            supplier="cez",
            source_url=SOURCE_URL,
            discovered_at=VALIDATED_AT,
            document_date=date(2026, 1, 1),
            content_type="application/pdf",
        ),
        product_name="Basic",
        valid_from=date(2026, 1, 1),
        match_score=100,
        match_reasons=("exact verified source",),
        price_scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    )
    fingerprint = selection.tariff_candidate_selection_fingerprint(candidate)
    return download.validate_selected_tariff_download(
        candidate=candidate,
        selected_fingerprint=fingerprint,
        status_code=200,
        final_url=SOURCE_URL,
        content_type="application/pdf",
        content=content,
        validated_at=VALIDATED_AT,
    )


def test_extracts_real_pdf_text_and_preserves_pinned_source_identity() -> None:
    sources, selection, download, extractor = load_modules()
    content = _minimal_text_pdf("CENIK BASIC D25d VT 3,960 NT 3,700")
    validated = _validated_download(sources, selection, download, content)

    result = extractor.extract_text_from_validated_tariff_pdf(validated)

    assert "CENIK BASIC D25d VT 3,960 NT 3,700" in result.text
    assert result.source_url == SOURCE_URL
    assert result.sha256 == hashlib.sha256(content).hexdigest()
    assert result.page_count == 1
    assert result.extracted_chars == len(result.text)
    assert result.extraction_method == "pypdf_text"
    assert result.parser_authorized is True
    assert result.persistence_performed is False
    assert result.activation_performed is False


def test_rejects_unvalidated_or_wrong_authority_input() -> None:
    _sources, _selection, _download, extractor = load_modules()

    with pytest.raises(ValueError, match="ValidatedTariffDownload"):
        extractor.extract_text_from_validated_tariff_pdf(object())


def test_encrypted_pdf_fails_closed() -> None:
    sources, selection, download, extractor = load_modules()
    validated = _validated_download(
        sources,
        selection,
        download,
        _writer_pdf(encrypted=True),
    )

    with pytest.raises(ValueError, match="encrypted tariff PDFs"):
        extractor.extract_text_from_validated_tariff_pdf(validated)


def test_page_count_limit_is_enforced_before_text_extraction() -> None:
    sources, selection, download, extractor = load_modules()
    validated = _validated_download(
        sources,
        selection,
        download,
        _writer_pdf(pages=2),
    )

    with pytest.raises(ValueError, match="page count"):
        extractor.extract_text_from_validated_tariff_pdf(validated, max_pages=1)


def test_extracted_text_size_limit_is_enforced() -> None:
    sources, selection, download, extractor = load_modules()
    validated = _validated_download(
        sources,
        selection,
        download,
        _minimal_text_pdf("A" * 200),
    )

    with pytest.raises(ValueError, match="extracted text exceeds"):
        extractor.extract_text_from_validated_tariff_pdf(validated, max_chars=32)


def test_pdf_without_text_layer_fails_closed_instead_of_invoking_ocr() -> None:
    sources, selection, download, extractor = load_modules()
    validated = _validated_download(
        sources,
        selection,
        download,
        _writer_pdf(),
    )

    with pytest.raises(ValueError, match="no extractable text layer"):
        extractor.extract_text_from_validated_tariff_pdf(validated)


def test_malformed_pdf_that_only_passed_transport_header_check_fails_closed() -> None:
    sources, selection, download, extractor = load_modules()
    validated = _validated_download(
        sources,
        selection,
        download,
        b"%PDF-1.7\nthis is not a structurally valid pdf",
    )

    with pytest.raises(ValueError, match="cannot be parsed safely"):
        extractor.extract_text_from_validated_tariff_pdf(validated)


def test_limits_must_be_positive_integers() -> None:
    sources, selection, download, extractor = load_modules()
    validated = _validated_download(
        sources,
        selection,
        download,
        _minimal_text_pdf("Basic"),
    )

    for kwargs in ({"max_pages": 0}, {"max_chars": 0}, {"max_pages": True}):
        with pytest.raises(ValueError, match="positive integer"):
            extractor.extract_text_from_validated_tariff_pdf(validated, **kwargs)
