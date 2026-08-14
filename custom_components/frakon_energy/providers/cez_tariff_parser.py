"""Pure parser for text extracted from ČEZ household commercial price-list PDFs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re
import unicodedata

_RATE_RE = re.compile(r"\bD\d{2}d\b", re.IGNORECASE)
_CZECH_NUMBER_RE = re.compile(
    r"(?<!\d)(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})*|\d+),\d{2}(?!\d)|[–—-]"
)
_VALID_FROM_RE = re.compile(
    r"účinnost\s+obchodních\s+cen\s+od\s+(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ParsedCezCommercialPrice:
    """One tariff column from an official ČEZ supplier-commercial price list."""

    product_name: str
    valid_from: date
    distribution_tariff: str
    high_rate_czk_per_kwh: Decimal
    low_rate_czk_per_kwh: Decimal | None
    supplier_standing_czk_month: Decimal
    includes_vat: bool = True

    def __post_init__(self) -> None:
        if not self.product_name.strip():
            raise ValueError("product_name must not be empty")
        if not _RATE_RE.fullmatch(self.distribution_tariff):
            raise ValueError("distribution_tariff must use a code such as D25d")
        for field_name in (
            "high_rate_czk_per_kwh",
            "supplier_standing_czk_month",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{field_name} must be a finite non-negative Decimal")
        if self.low_rate_czk_per_kwh is not None:
            value = self.low_rate_czk_per_kwh
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError("low_rate_czk_per_kwh must be a finite non-negative Decimal")
        if self.includes_vat is not True:
            raise ValueError("ČEZ commercial parser currently accepts only VAT-included rows")


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _parse_czech_decimal(token: str) -> Decimal | None:
    value = token.strip()
    if value in {"-", "–", "—"}:
        return None
    normalized = (
        value.replace(" ", "")
        .replace("\u00a0", "")
        .replace("\u202f", "")
        .replace(",", ".")
    )
    try:
        result = Decimal(normalized)
    except InvalidOperation as err:
        raise ValueError(f"invalid Czech decimal: {token}") from err
    if not result.is_finite() or result < 0:
        raise ValueError(f"invalid non-negative price: {token}")
    return result


def _normalize_tariff(value: str) -> str:
    tariff = value.strip()
    if not tariff:
        raise ValueError("distribution_tariff is required")
    tariff = tariff[0].upper() + tariff[1:-1] + tariff[-1].lower()
    if not _RATE_RE.fullmatch(tariff):
        raise ValueError("distribution_tariff must use a code such as D25d")
    return tariff


def _extract_product(lines: tuple[str, ...]) -> str:
    for index, line in enumerate(lines):
        if "ceník elektřiny pro domácnosti" in line.casefold():
            for candidate in lines[index + 1 :]:
                if candidate.strip():
                    return candidate.strip()
    raise ValueError("ČEZ household price-list title was not found")


def _extract_valid_from(text: str) -> date:
    match = _VALID_FROM_RE.search(text)
    if match is None:
        raise ValueError("ČEZ commercial price validity date was not found")
    day, month, year = (int(item) for item in match.groups())
    try:
        return date(year, month, day)
    except ValueError as err:
        raise ValueError("ČEZ commercial price validity date is invalid") from err


def _extract_table_rows(
    lines: tuple[str, ...], rate_count: int, header_index: int
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []
    for line in lines[header_index + 1 :]:
        if "(" in line or ")" in line:
            # Parenthesized rows are prices without VAT in the current ČEZ layout.
            continue
        tokens = tuple(match.group(0) for match in _CZECH_NUMBER_RE.finditer(line))
        if len(tokens) == rate_count:
            rows.append(tokens)
            if len(rows) == 3:
                break
    if len(rows) != 3:
        raise ValueError("ČEZ commercial VT/NT/standing-price rows were not found")
    return rows[0], rows[1], rows[2]


def parse_cez_commercial_price_text(
    text: str,
    *,
    distribution_tariff: str,
) -> ParsedCezCommercialPrice:
    """Parse one tariff column from extracted text of a ČEZ commercial PDF.

    The parser intentionally accepts only the current household-commercial
    document contract: it must state that the PDF contains only the commercial
    (unregulated) portion and that bold values include 21% VAT.  This prevents
    downstream callers from mistaking these supplier values for an all-in price.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("price-list text must not be empty")

    folded = _fold(text)
    if "uvadime jen obchodni" not in folded or "neregulovanou" not in folded:
        raise ValueError("document is not marked as a supplier-commercial ČEZ price list")
    if "21% dph" not in folded or "v zavorce bez dph" not in folded:
        raise ValueError("ČEZ VAT row convention was not found")

    lines = tuple(line.strip() for line in text.splitlines() if line.strip())
    product_name = _extract_product(lines)
    valid_from = _extract_valid_from(text)
    tariff = _normalize_tariff(distribution_tariff)

    header_index: int | None = None
    rates: tuple[str, ...] = ()
    for index, line in enumerate(lines):
        found = tuple(_normalize_tariff(item) for item in _RATE_RE.findall(line))
        if len(found) >= 2:
            header_index = index
            rates = found
            break
    if header_index is None:
        raise ValueError("ČEZ distribution-tariff header was not found")
    try:
        tariff_index = rates.index(tariff)
    except ValueError as err:
        raise ValueError(f"distribution tariff is not present in document: {tariff}") from err

    high_row, low_row, standing_row = _extract_table_rows(
        lines, len(rates), header_index
    )
    high_mwh = _parse_czech_decimal(high_row[tariff_index])
    low_mwh = _parse_czech_decimal(low_row[tariff_index])
    standing = _parse_czech_decimal(standing_row[tariff_index])
    if high_mwh is None or standing is None:
        raise ValueError("ČEZ high-tariff and standing prices must be numeric")

    return ParsedCezCommercialPrice(
        product_name=product_name,
        valid_from=valid_from,
        distribution_tariff=tariff,
        high_rate_czk_per_kwh=high_mwh / Decimal("1000"),
        low_rate_czk_per_kwh=(
            low_mwh / Decimal("1000") if low_mwh is not None else None
        ),
        supplier_standing_czk_month=standing,
    )
