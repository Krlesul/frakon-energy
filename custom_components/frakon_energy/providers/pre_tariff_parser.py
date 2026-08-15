"""Strict parser for PRE household supplier-commercial electricity price lists."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re
import unicodedata

_VAT_RATE = Decimal("1.21")
_MWH_TO_KWH = Decimal("1000")
_VALID_FROM_RE = re.compile(
    r"cenik elektriny pro domacnosti platny od (\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})",
    re.IGNORECASE,
)
_PRICE_TOKEN_RE = re.compile(
    r"(?<!\d)(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})*|\d+),\d{2}(?!\d)|[–—]"
)
_REGULATED_SECTION_RE = re.compile(
    r"(?:^\s*DISTRIBUČNÍ\s+SAZBA\s*$|"
    r"CENA\s+ZA\s+DISTRIBUOVANÉ\s+MNOŽSTVÍ\s+ELEKTŘINY|"
    r"CENA\s+ZA\s+SOUVISEJÍCÍ\s+SLUŽBY\s+V\s+ELEKTROENERGETICE)",
    re.IGNORECASE | re.MULTILINE,
)

# PRE's first-page supplier table has eight commercial columns. Some columns
# cover more than one distribution tariff but share one supplier price.
_TARIFF_COLUMN = {
    "D01d": 0,
    "D02d": 0,
    "D25d": 1,
    "D26d": 1,
    "D27d": 2,
    "D35d": 3,
    "D45d": 4,
    "D56d": 5,
    "D57d": 6,
    "D61d": 7,
}
_TERRITORY_MARKERS = {
    "pre_distribuce": "na distribucnim uzemi predistribuce",
    "eg_d": "na distribucnim uzemi eg.d",
    "cez_distribuce": "na distribucnim uzemi cez distribuce",
}


@dataclass(frozen=True, slots=True)
class ParsedPreSupplierTariff:
    """One exact supplier-commercial PRE tariff column."""

    product_name: str
    valid_from: date
    distribution_tariff: str
    high_rate_czk_per_kwh: Decimal
    low_rate_czk_per_kwh: Decimal | None
    supplier_standing_czk_month: Decimal
    includes_vat: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.product_name, str) or not self.product_name.strip():
            raise ValueError("product_name must not be empty")
        if not isinstance(self.valid_from, date):
            raise ValueError("valid_from must be a date")
        if self.distribution_tariff not in _TARIFF_COLUMN:
            raise ValueError("unsupported PRE distribution tariff")
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
            raise ValueError("PRE parser currently accepts only VAT-included prices")


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(
        normalized.replace("\u00a0", " ").replace("\u202f", " ").split()
    )


def _parse_decimal(token: str) -> Decimal | None:
    if token in {"–", "—"}:
        return None
    normalized = (
        token.replace("\u00a0", " ")
        .replace("\u202f", " ")
        .replace(" ", "")
        .replace(",", ".")
    )
    try:
        result = Decimal(normalized)
    except InvalidOperation as err:
        raise ValueError(f"invalid PRE price token: {token}") from err
    if not result.is_finite() or result < 0:
        raise ValueError(f"invalid PRE non-negative price token: {token}")
    return result


def _validate_vat_pair(gross: Decimal, net: Decimal, *, field: str) -> None:
    if abs(gross - net * _VAT_RATE) > Decimal("0.01"):
        raise ValueError(f"PRE {field} gross/net pair is inconsistent with 21% VAT")


def _parse_valid_from(folded: str) -> date:
    match = _VALID_FROM_RE.search(folded)
    if match is None:
        raise ValueError("PRE commercial validity date was not found")
    day, month, year = (int(value) for value in match.groups())
    try:
        return date(year, month, day)
    except ValueError as err:
        raise ValueError("PRE commercial validity date is invalid") from err


def _commercial_tokens(text: str) -> tuple[str, ...]:
    """Return only the exact supplier matrix before any regulated authority.

    The first occurrence of the monthly supplier heading is followed by exactly:
    8 gross/net VT pairs, then one single-rate dash + 7 gross/net NT pairs, then
    one gross/net supplier standing charge. The tail is structurally cut at the
    first regulated-section marker before tokenization, so a missing supplier
    cell can never be filled by a later regulated price.
    """
    folded = _fold(text)
    heading = "mesicni plat za odberne misto"
    heading_pos = folded.find(heading)
    if heading_pos < 0:
        raise ValueError("PRE supplier standing-charge heading was not found")

    # Folded and original strings do not have identical character offsets when
    # accents are removed, so locate the heading in original text independently.
    original_heading = re.search(
        r"M[eě]s[ií]čn[ií]\s+plat\s+za\s+odb[eě]rn[eé]\s+m[ií]sto",
        text,
        re.IGNORECASE,
    )
    if original_heading is None:
        raise ValueError("PRE supplier standing-charge heading was not found")
    tail = text[original_heading.end() :]
    regulated = _REGULATED_SECTION_RE.search(tail)
    if regulated is not None:
        tail = tail[: regulated.start()]

    tokens = tuple(match.group(0) for match in _PRICE_TOKEN_RE.finditer(tail))
    if len(tokens) < 33:
        raise ValueError("PRE supplier-commercial price matrix is incomplete")
    if len(tokens) > 33:
        raise ValueError("PRE supplier-commercial price matrix is ambiguous")
    return tokens


def _decode_matrix(
    tokens: tuple[str, ...],
) -> tuple[tuple[Decimal, ...], tuple[Decimal | None, ...], Decimal]:
    if len(tokens) != 33:
        raise ValueError("PRE supplier-commercial price matrix has unexpected size")

    high: list[Decimal] = []
    for index in range(8):
        gross = _parse_decimal(tokens[index * 2])
        net = _parse_decimal(tokens[index * 2 + 1])
        if gross is None or net is None:
            raise ValueError("PRE VT commercial price pair must be numeric")
        _validate_vat_pair(gross, net, field=f"VT column {index + 1}")
        high.append(gross)

    cursor = 16
    low: list[Decimal | None] = []
    first_low = _parse_decimal(tokens[cursor])
    if first_low is not None:
        raise ValueError("PRE single-rate commercial NT cell must be a dash")
    low.append(None)
    cursor += 1
    for index in range(1, 8):
        gross = _parse_decimal(tokens[cursor])
        net = _parse_decimal(tokens[cursor + 1])
        if gross is None or net is None:
            raise ValueError("PRE dual-rate commercial NT pair must be numeric")
        _validate_vat_pair(gross, net, field=f"NT column {index + 1}")
        low.append(gross)
        cursor += 2

    standing_gross = _parse_decimal(tokens[cursor])
    standing_net = _parse_decimal(tokens[cursor + 1])
    if standing_gross is None or standing_net is None:
        raise ValueError("PRE supplier standing-charge pair must be numeric")
    _validate_vat_pair(standing_gross, standing_net, field="standing")
    return tuple(high), tuple(low), standing_gross


def parse_pre_supplier_tariff(
    text: str,
    *,
    expected_product_name: str,
    expected_distribution_tariff: str,
    expected_distributor: str,
    expected_valid_from: date,
) -> ParsedPreSupplierTariff:
    """Parse one exact PRE supplier-commercial price and reject source drift."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("PRE tariff text must not be empty")
    if not isinstance(expected_product_name, str) or not expected_product_name.strip():
        raise ValueError("expected_product_name must not be empty")
    if expected_distribution_tariff not in _TARIFF_COLUMN:
        raise LookupError(
            f"unsupported PRE distribution tariff: {expected_distribution_tariff}"
        )
    if not isinstance(expected_valid_from, date):
        raise ValueError("expected_valid_from must be a date")
    try:
        territory_marker = _TERRITORY_MARKERS[expected_distributor]
    except KeyError as err:
        raise LookupError(f"unsupported PRE distributor: {expected_distributor}") from err

    folded = _fold(text)
    if _fold(expected_product_name) not in folded:
        raise ValueError("PRE tariff document product marker does not match expected product")
    if territory_marker not in folded:
        raise ValueError("PRE tariff document distribution territory does not match candidate")
    if "cena za spotrebovanou elektrinu" not in folded:
        raise ValueError("PRE tariff document is missing supplier-commercial marker")
    if "ceny uvedene tucne jsou vcetne dph ve vysi 21 %" not in folded:
        raise ValueError("PRE tariff document is missing explicit 21% VAT convention")

    valid_from = _parse_valid_from(folded)
    if valid_from != expected_valid_from:
        raise ValueError(
            "PRE tariff document validity does not match immutable selected candidate"
        )

    tokens = _commercial_tokens(text)
    high, low, standing = _decode_matrix(tokens)
    column = _TARIFF_COLUMN[expected_distribution_tariff]
    return ParsedPreSupplierTariff(
        product_name=expected_product_name.strip(),
        valid_from=valid_from,
        distribution_tariff=expected_distribution_tariff,
        high_rate_czk_per_kwh=high[column] / _MWH_TO_KWH,
        low_rate_czk_per_kwh=(
            None if low[column] is None else low[column] / _MWH_TO_KWH
        ),
        supplier_standing_czk_month=standing,
        includes_vat=True,
    )
