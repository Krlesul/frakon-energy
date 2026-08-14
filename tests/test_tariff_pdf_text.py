from datetime import date, datetime, timezone
import hashlib
import importlib.util
from pathlib import Path
import sys
import types

import pytest


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
    _load(
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components/frakon_energy/tariff_candidate_selection.py",
    )
    download = _load(
        "custom_components.frakon_energy.tariff_download",
        "custom_components/frakon_energy/tariff_download.py",
    )
    pdf_text = _load(
        "custom_components.frakon_energy.tariff_pdf_text",
        "custom_components/frakon_energy/tariff_pdf_text.py",
    )
    return sources, download, pdf_text


def _pdf_bytes(*page_texts: str) -> bytes:
    """Build a tiny deterministic text PDF fixture without external generators."""
    if not page_texts:
        page_texts = ("Tariff fixture",)
    page_count = len(page_texts)
    font_id = 3 + page_count
    first_content_id = font_id + 1
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            b"<< /Type /Pages /Kids ["
            + b" ".join(f"{3 + index} 0 R".encode() for index in range(page_count))
            + f"] /Count {page_count} >>".encode()
        ),
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for index, text in enumerate(page_texts):
        page_id = 3 + index
        content_id = first_content_id + index
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode()
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects[content_id] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode()
            + stream
            + b"\nendstream"
        )

    max_id = max(objects)
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0] * (max_id + 1)
    for object_id in range(1, max_id + 1):
        offsets[object_id] = len(data)
        data.extend(f"{object_id} 0 obj\n".encode())
        data.extend(objects[object_id])
        data.extend(b"\nendobj\n")

    xref = len(data)
    data.extend(f"xref\n0 {max_id + 1}\n0000000000 65535 f \n".encode())
    for object_id in range(1, max_id + 1):
        data.extend(f"{offsets[object_id]:010d} 00000 n \n".encode())
    data.extend(
        (
            f"trailer\n<< /Size {max_id + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    return bytes(data)


def _download(sources, download_module, content: bytes, *, parser_authorized=True):
    candidate = sources.TariffDocumentCandidate(
        document=sources.OfficialTariffDocument(
            supplier="cez",
            source_url="https://www.cez.cz/file/fixture.pdf",
            discovered_at=datetime(2026, 8, 14, 15, 30, tzinfo=timezone.utc),
            document_date=date(2026, 1, 1),
            content_type="application/pdf",
        ),
        product_name="Basic",
        valid_from=date(2026, 1, 1),
        match_score=100,
        match_reasons=("fixture",),
        price_scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    )
    document = sources.OfficialTariffDocument(
        supplier="cez",
        source_url=candidate.document.source_url,
        discovered_at=candidate.document.discovered_at,
        document_date=candidate.document.document_date,
        sha256=hashlib.sha256(content).hexdigest(),
        content_type="application/pdf",
    )
    return download_module.ValidatedTariffDownload(
        selected_fingerprint="a" * 64,
        candidate=candidate,
        document=document,
        content=content,
        validated_at=datetime(2026, 8, 14, 15, 31, tzinfo=timezone.utc),
        parser_authorized=parser_authorized,
    )


def test_extracts_layout_text_only_from_validated_parser_authorized_download() -> None:
    sources, download_module, pdf_text = load_modules()
    content = _pdf_bytes("CEZ BASIC D25d", "VT 3 000,00 NT 2 500,00")
    download = _download(sources, download_module, content)

    result = pdf_text.extract_validated_tariff_pdf_text(download)

    assert result.source_url == "https://www.cez.cz/file/fixture.pdf"
    assert result.document_sha256 == hashlib.sha256(content).hexdigest()
    assert result.page_count == 2
    assert "CEZ BASIC D25d" in result.text
    assert "VT 3 000,00 NT 2 500,00" in result.text
    assert result.extraction_method == "pypdf_layout"
    assert result.parser_authorized is True
    assert result.persistence_performed is False
    assert result.activation_performed is False


def test_rejects_arbitrary_bytes_or_non_authorized_download() -> None:
    sources, download_module, pdf_text = load_modules()

    with pytest.raises(ValueError, match="ValidatedTariffDownload"):
        pdf_text.extract_validated_tariff_pdf_text(_pdf_bytes("not wrapped"))

    download = _download(
        sources,
        download_module,
        _pdf_bytes("not authorized"),
        parser_authorized=False,
    )
    with pytest.raises(ValueError, match="not authorized for parsing"):
        pdf_text.extract_validated_tariff_pdf_text(download)


def test_page_count_and_text_size_are_bounded_before_parser_use() -> None:
    sources, download_module, pdf_text = load_modules()
    two_pages = _download(
        sources,
        download_module,
        _pdf_bytes("page one", "page two"),
    )

    with pytest.raises(ValueError, match="page count"):
        pdf_text.extract_validated_tariff_pdf_text(two_pages, max_pages=1)

    with pytest.raises(ValueError, match="text exceeds"):
        pdf_text.extract_validated_tariff_pdf_text(two_pages, max_text_chars=5)


def test_invalid_pdf_and_empty_extractable_text_fail_closed() -> None:
    sources, download_module, pdf_text = load_modules()
    invalid = _download(sources, download_module, b"%PDF-not-a-real-document")
    with pytest.raises(ValueError, match="could not be opened"):
        pdf_text.extract_validated_tariff_pdf_text(invalid)

    blank = _download(sources, download_module, _pdf_bytes(""))
    with pytest.raises(ValueError, match="no extractable text"):
        pdf_text.extract_validated_tariff_pdf_text(blank)


def test_custom_limits_cannot_exceed_global_safety_caps() -> None:
    sources, download_module, pdf_text = load_modules()
    download = _download(sources, download_module, _pdf_bytes("fixture"))

    with pytest.raises(ValueError, match="max_pages"):
        pdf_text.extract_validated_tariff_pdf_text(
            download,
            max_pages=pdf_text.MAX_TARIFF_PDF_PAGES + 1,
        )
    with pytest.raises(ValueError, match="max_text_chars"):
        pdf_text.extract_validated_tariff_pdf_text(
            download,
            max_text_chars=pdf_text.MAX_TARIFF_EXTRACTED_TEXT_CHARS + 1,
        )
