"""Bounded PDF text extraction for already validated tariff documents."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import re

from pypdf import PdfReader

from .tariff_download import MAX_TARIFF_DOCUMENT_BYTES, ValidatedTariffDownload

MAX_TARIFF_PDF_PAGES = 32
MAX_TARIFF_EXTRACTED_TEXT_CHARS = 1_000_000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ExtractedTariffPdfText:
    """Text extracted from the exact SHA-pinned PDF selected by the user."""

    source_url: str
    document_sha256: str
    page_count: int
    text: str
    extraction_method: str = "pypdf_layout"
    parser_authorized: bool = True
    persistence_performed: bool = False
    activation_performed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.source_url, str) or not self.source_url.strip():
            raise ValueError("source_url must not be empty")
        if (
            not isinstance(self.document_sha256, str)
            or not _SHA256_RE.fullmatch(self.document_sha256)
        ):
            raise ValueError("document_sha256 must be a lowercase SHA-256 hex digest")
        if isinstance(self.page_count, bool) or not isinstance(self.page_count, int):
            raise ValueError("page_count must be an integer")
        if not 1 <= self.page_count <= MAX_TARIFF_PDF_PAGES:
            raise ValueError("page_count is outside the allowed tariff PDF range")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("extracted tariff text must not be empty")
        if len(self.text) > MAX_TARIFF_EXTRACTED_TEXT_CHARS:
            raise ValueError("extracted tariff text exceeds maximum allowed size")
        if self.extraction_method != "pypdf_layout":
            raise ValueError("unsupported tariff PDF extraction method")
        if self.parser_authorized is not True:
            raise ValueError("extracted tariff text must remain parser-authorized")
        if (
            self.persistence_performed is not False
            or self.activation_performed is not False
        ):
            raise ValueError("PDF text extraction must not persist or activate a tariff")


def extract_validated_tariff_pdf_text(
    download: ValidatedTariffDownload,
    *,
    max_pages: int = MAX_TARIFF_PDF_PAGES,
    max_text_chars: int = MAX_TARIFF_EXTRACTED_TEXT_CHARS,
) -> ExtractedTariffPdfText:
    """Extract bounded layout text only from a selected, validated tariff PDF.

    This function deliberately accepts ``ValidatedTariffDownload`` rather than
    arbitrary bytes. The caller therefore cannot skip candidate fingerprint,
    official source URL, PDF/MIME, size or SHA-256 validation performed by the
    preceding download boundary.
    """
    if not isinstance(download, ValidatedTariffDownload):
        raise ValueError("download must be ValidatedTariffDownload")
    if download.parser_authorized is not True:
        raise ValueError("validated tariff download is not authorized for parsing")
    if (
        download.persistence_performed is not False
        or download.activation_performed is not False
    ):
        raise ValueError("parser preview cannot consume an activation-bearing download")
    if len(download.content) > MAX_TARIFF_DOCUMENT_BYTES:
        raise ValueError("validated tariff PDF exceeds maximum allowed size")
    if (
        isinstance(max_pages, bool)
        or not isinstance(max_pages, int)
        or not 1 <= max_pages <= MAX_TARIFF_PDF_PAGES
    ):
        raise ValueError("max_pages must be within the tariff PDF page limit")
    if (
        isinstance(max_text_chars, bool)
        or not isinstance(max_text_chars, int)
        or not 1 <= max_text_chars <= MAX_TARIFF_EXTRACTED_TEXT_CHARS
    ):
        raise ValueError("max_text_chars must be within the tariff text limit")

    try:
        reader = PdfReader(BytesIO(download.content), strict=False)
    except Exception as err:
        raise ValueError("validated tariff PDF could not be opened") from err

    try:
        encrypted = reader.is_encrypted
    except Exception as err:
        raise ValueError("tariff PDF encryption state could not be read") from err
    if encrypted:
        raise ValueError("encrypted tariff PDFs are not supported")

    try:
        page_count = len(reader.pages)
    except Exception as err:
        raise ValueError("tariff PDF page count could not be read") from err
    if page_count < 1:
        raise ValueError("tariff PDF must contain at least one page")
    if page_count > max_pages:
        raise ValueError("tariff PDF exceeds maximum allowed page count")

    extracted_pages: list[str] = []
    text_chars = 0
    for page in reader.pages:
        try:
            page_text = page.extract_text(
                extraction_mode="layout",
                layout_mode_space_vertically=False,
            )
        except Exception as err:
            raise ValueError("tariff PDF text extraction failed") from err
        if page_text is None:
            page_text = ""
        if not isinstance(page_text, str):
            raise ValueError("tariff PDF extractor returned invalid text")
        page_text = page_text.strip()
        if not page_text:
            continue
        additional = len(page_text) + (1 if extracted_pages else 0)
        text_chars += additional
        if text_chars > max_text_chars:
            raise ValueError("extracted tariff text exceeds maximum allowed size")
        extracted_pages.append(page_text)

    text = "\n".join(extracted_pages).strip()
    if not text:
        raise ValueError("tariff PDF contains no extractable text")

    return ExtractedTariffPdfText(
        source_url=download.document.source_url,
        document_sha256=download.document.sha256 or "",
        page_count=page_count,
        text=text,
    )
