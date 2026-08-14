"""Bounded text extraction from already validated tariff PDF downloads."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .tariff_download import ValidatedTariffDownload

MAX_TARIFF_PDF_PAGES = 50
MAX_TARIFF_EXTRACTED_TEXT_CHARS = 1_000_000


@dataclass(frozen=True, slots=True)
class ExtractedTariffPdfText:
    """Text extracted from one cryptographically pinned tariff document."""

    source_url: str
    sha256: str
    page_count: int
    text: str
    extracted_chars: int
    extraction_method: str = "pypdf_text"
    parser_authorized: bool = True
    persistence_performed: bool = False
    activation_performed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.source_url, str) or not self.source_url.strip():
            raise ValueError("source_url must not be empty")
        if not isinstance(self.sha256, str) or len(self.sha256) != 64:
            raise ValueError("sha256 must be a 64-character digest")
        if isinstance(self.page_count, bool) or not isinstance(self.page_count, int) or self.page_count <= 0:
            raise ValueError("page_count must be a positive integer")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("extracted tariff text must not be empty")
        if self.extracted_chars != len(self.text):
            raise ValueError("extracted_chars must match text length")
        if self.extraction_method != "pypdf_text":
            raise ValueError("unsupported tariff PDF extraction method")
        if self.parser_authorized is not True:
            raise ValueError("extracted tariff text must remain parser-authorized")
        if self.persistence_performed is not False or self.activation_performed is not False:
            raise ValueError("PDF text extraction has no persistence or activation authority")


def _positive_limit(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def extract_text_from_validated_tariff_pdf(
    download: ValidatedTariffDownload,
    *,
    max_pages: int = MAX_TARIFF_PDF_PAGES,
    max_chars: int = MAX_TARIFF_EXTRACTED_TEXT_CHARS,
) -> ExtractedTariffPdfText:
    """Extract text only after the selected-document validation boundary.

    The function intentionally performs no OCR. Official PDFs without a usable
    text layer fail closed and must be handled by a separately reviewed parser
    path rather than silently invoking an unbounded image/OCR pipeline.
    """
    if not isinstance(download, ValidatedTariffDownload):
        raise ValueError("download must be ValidatedTariffDownload")
    if download.parser_authorized is not True:
        raise ValueError("tariff download is not authorized for parsing")

    page_limit = _positive_limit(max_pages, "max_pages")
    char_limit = _positive_limit(max_chars, "max_chars")

    try:
        reader = PdfReader(BytesIO(download.content), strict=True)
    except (PdfReadError, ValueError, TypeError) as err:
        raise ValueError("tariff PDF cannot be parsed safely") from err

    if reader.is_encrypted:
        raise ValueError("encrypted tariff PDFs are not supported")

    try:
        page_count = len(reader.pages)
    except Exception as err:
        raise ValueError("tariff PDF page tree cannot be read safely") from err
    if page_count <= 0:
        raise ValueError("tariff PDF contains no pages")
    if page_count > page_limit:
        raise ValueError("tariff PDF exceeds maximum allowed page count")

    chunks: list[str] = []
    extracted_chars = 0
    for index, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as err:
            raise ValueError(f"tariff PDF text extraction failed on page {index}") from err
        if not isinstance(page_text, str):
            raise ValueError(f"tariff PDF page {index} returned invalid text")
        if extracted_chars + len(page_text) > char_limit:
            raise ValueError("tariff PDF extracted text exceeds maximum allowed size")
        chunks.append(page_text)
        extracted_chars += len(page_text)

    text = "\n\n".join(chunks).strip()
    if not text:
        raise ValueError("tariff PDF contains no extractable text layer")
    if len(text) > char_limit:
        raise ValueError("tariff PDF extracted text exceeds maximum allowed size")

    return ExtractedTariffPdfText(
        source_url=download.document.source_url,
        sha256=download.document.sha256 or "",
        page_count=page_count,
        text=text,
        extracted_chars=len(text),
    )
