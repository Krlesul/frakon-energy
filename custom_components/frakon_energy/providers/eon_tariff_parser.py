"""Strict parser for E.ON household electricity supplier-commercial price lists."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re
import unicodedata

from .eon_tariffs import EON_PRODUCT_PERIODS

_VAT_RATE = Decimal("1.21")
_MWH_TO_KWH = Decimal("1000")
_REGULATED_MARKER = "Regulovaná cena za související služby v elektroenergetice"
_COMMERCIAL_MARKER = "Obchodní cena za dodávku elektřiny"
_VAT_MARKER = "Tučně uvedené ceny jsou včetně 21% DPH"
_OVERALL_VALID_FROM_RE = re.compile(
    r"Obchodní\s+cena\s+za\s+elektřinu\s+platná\s+od\s+(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})",
    re.IGNORECASE,
)
# Price-cell separators are deliberately horizontal only. Using ``\s`` here
# would let a regex consume a newline and merge independent PDF table rows.
_HORIZONTAL_SPACE = r"[ \u00a0\u202f]"
_HORIZONTAL_PADDING = r"[ \t\u00a0\u202f]*"
_CONCATENATED_PRICE_LINE_RE = re.compile(
    rf"^{_HORIZONTAL_PADDING}"
    rf"(?P<first>\d{{1,3}}{_HORIZONTAL_SPACE}\d{{3}})"
    rf"(?P<second>\d{{1,3}}{_HORIZONTAL_SPACE}\d{{3}})"
    rf"{_HORIZONTAL_PADDING}$"
)
_PRICE_TOKEN_RE = re.compile(
    rf"(?<!\d)(?:\d{{1,3}}(?:{_HORIZONTAL_SPACE}\d{{3}})+|\d{{1,4}})(?:,\d{{1,2}})?(?!\d)|[–—]"
)

_RATE_GROUPS: tuple[tuple[str, ...], ...] = (
    ("D01d", "D02d"),
    ("D25d", "D26d", "D27d"),
    ("D35d",),
    ("D45d", "D56d", "D57d"),
    ("D61d",),
)
_EON_DOCUMENT_VALID_FROM = {
    "Variant PRO na 2 roky": date(2026, 3, 30),
    "Elektřina výhodně PRO na 3 roky": date(2026, 6, 17),
}
_EON_DOCUMENT_PRICE_BLOCKS = {
    "Variant PRO na 2 roky": 1,
    # The PDF prints 2026, 2027, 2028 and 2029+ columns. The latter three must
    # stay identical because they are one fixed customer price authority.
    "Elektřina výhodně PRO na 3 roky": 4,
}


@dataclass(frozen=True, slots=True)
class ParsedEonSupplierTariff:
    """Supplier-only E.ON prices selected from one exact advertised period."""

    product_name: str
    valid_from: date
    valid_to: date | None
    distribution_tariff: str
    high_rate_czk_per_kwh: Decimal
    low_rate_czk_per_kwh: Decimal | None
    supplier_standing_czk_month: Decimal
    includes_vat: bool = True


@dataclass(frozen=True, slots=True)
class _GrossPriceBlock:
    high_rate_czk_mwh: Decimal
    low_rate_czk_mwh: Decimal | None
    standing_czk_month: Decimal


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(normalized.replace("\u00a0", " ").replace("\u202f", " ").split())


def _parse_date_match(match: re.Match[str]) -> date:
    try:
        return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    except ValueError as err:
        raise ValueError("E.ON commercial validity marker contains an invalid date") from err


def _period_index(
    product_name: str,
    expected_valid_from: date,
    expected_valid_to: date | None,
) -> int:
    try:
        periods = EON_PRODUCT_PERIODS[product_name]
    except KeyError as err:
        raise LookupError(f"unsupported E.ON product parser: {product_name}") from err
    for index, period in enumerate(periods):
        if period.valid_from == expected_valid_from and period.valid_to == expected_valid_to:
            return index
    raise ValueError(
        "E.ON candidate validity does not match a verified commercial price period"
    )


def _group_for_tariff(distribution_tariff: str) -> tuple[int, tuple[str, ...]]:
    for index, group in enumerate(_RATE_GROUPS):
        if distribution_tariff in group:
            return index, group
    raise LookupError(f"unsupported E.ON distribution tariff: {distribution_tariff}")


def _first_tariff_position(text: str, tariff: str, *, start: int = 0) -> int:
    match = re.search(rf"(?<![A-Za-z0-9]){re.escape(tariff)}(?![A-Za-z0-9])", text[start:])
    return -1 if match is None else start + match.start()


def _target_rate_segment(commercial_text: str, distribution_tariff: str) -> str:
    group_index, group = _group_for_tariff(distribution_tariff)
    start = _first_tariff_position(commercial_text, group[0])
    if start < 0:
        raise LookupError(
            f"E.ON commercial table does not contain tariff group for {distribution_tariff}"
        )

    end = len(commercial_text)
    if group_index + 1 < len(_RATE_GROUPS):
        next_start = _first_tariff_position(
            commercial_text,
            _RATE_GROUPS[group_index + 1][0],
            start=start + len(group[0]),
        )
        if next_start >= 0:
            end = next_start
    else:
        for marker in (
            "Celková cena elektřiny zahrnuje",
            "Poplatky související s dodávkou elektřiny",
        ):
            marker_pos = commercial_text.find(marker, start)
            if marker_pos >= 0:
                end = min(end, marker_pos)

    segment = commercial_text[start:end]
    data_start = 0
    for tariff in group:
        for match in re.finditer(
            rf"(?<![A-Za-z0-9]){re.escape(tariff)}(?![A-Za-z0-9])",
            segment,
        ):
            data_start = max(data_start, match.end())
    if data_start == 0:
        raise ValueError("E.ON commercial tariff group boundary could not be resolved")
    return segment[data_start:]


def _separate_concatenated_prices(value: str) -> str:
    """Split adjacent PDF cells such as ``3 3202 744`` on exact rows only."""
    separated_lines: list[str] = []
    for line in value.splitlines():
        match = _CONCATENATED_PRICE_LINE_RE.fullmatch(line)
        if match is None:
            separated_lines.append(line)
            continue
        separated_lines.extend((match.group("first"), match.group("second")))
    return "\n".join(separated_lines)


def _price_or_dash(token: str) -> Decimal | None:
    if token in {"–", "—"}:
        return None
    normalized = (
        token.replace("\u00a0", " ")
        .replace("\u202f", " ")
        .replace(" ", "")
        .replace(",", ".")
    )
    try:
        value = Decimal(normalized)
    except InvalidOperation as err:
        raise ValueError(f"invalid E.ON price token: {token}") from err
    if value < 0:
        raise ValueError("E.ON commercial price cannot be negative")
    return value


def _validate_vat_pair(gross: Decimal, net: Decimal, *, field: str) -> None:
    expected = net * _VAT_RATE
    # Page-one summaries round gross display values to whole CZK while the
    # detailed current-year table carries cents. The tolerance permits display
    # rounding only; it is far too small to accept a different price row.
    if abs(gross - expected) > Decimal("0.75"):
        raise ValueError(f"E.ON {field} gross/net pair is inconsistent with 21% VAT")


def _all_price_blocks(segment: str, *, block_count: int) -> tuple[_GrossPriceBlock, ...]:
    normalized = _separate_concatenated_prices(segment)
    tokens = [match.group(0).strip() for match in _PRICE_TOKEN_RE.finditer(normalized)]
    expected_tokens = block_count * 6
    if len(tokens) != expected_tokens:
        raise ValueError(
            "E.ON commercial tariff row does not contain the exact expected price matrix"
        )

    blocks: list[_GrossPriceBlock] = []
    for block_index in range(block_count):
        offset = block_index * 6
        gross_vt = _price_or_dash(tokens[offset])
        net_vt = _price_or_dash(tokens[offset + 1])
        gross_nt = _price_or_dash(tokens[offset + 2])
        net_nt = _price_or_dash(tokens[offset + 3])
        gross_fixed = _price_or_dash(tokens[offset + 4])
        net_fixed = _price_or_dash(tokens[offset + 5])

        if gross_vt is None or net_vt is None:
            raise ValueError("E.ON commercial VT price pair is missing")
        if gross_fixed is None or net_fixed is None:
            raise ValueError("E.ON supplier standing price pair is missing")
        if (gross_nt is None) != (net_nt is None):
            raise ValueError("E.ON commercial NT gross/net availability is inconsistent")

        _validate_vat_pair(gross_vt, net_vt, field="VT")
        if gross_nt is not None and net_nt is not None:
            _validate_vat_pair(gross_nt, net_nt, field="NT")
        _validate_vat_pair(gross_fixed, net_fixed, field="standing")
        blocks.append(
            _GrossPriceBlock(
                high_rate_czk_mwh=gross_vt,
                low_rate_czk_mwh=gross_nt,
                standing_czk_month=gross_fixed,
            )
        )
    return tuple(blocks)


def _select_semantic_price_block(
    product_name: str,
    semantic_period_index: int,
    blocks: tuple[_GrossPriceBlock, ...],
) -> _GrossPriceBlock:
    if product_name == "Variant PRO na 2 roky":
        if len(blocks) != 1 or semantic_period_index != 0:
            raise ValueError("E.ON Variant PRO price matrix is inconsistent")
        return blocks[0]

    if product_name == "Elektřina výhodně PRO na 3 roky":
        if len(blocks) != 4:
            raise ValueError("E.ON three-year price matrix is inconsistent")
        if blocks[1] != blocks[2] or blocks[1] != blocks[3]:
            raise ValueError(
                "E.ON fixed 2027+ price columns disagree; refusing ambiguous future price"
            )
        if semantic_period_index == 0:
            return blocks[0]
        if semantic_period_index == 1:
            return blocks[1]
        raise ValueError("E.ON three-year semantic price period is unsupported")

    raise LookupError(f"unsupported E.ON product parser: {product_name}")


def parse_eon_supplier_tariff(
    text: str,
    *,
    expected_product_name: str,
    expected_distribution_tariff: str,
    expected_valid_from: date,
    expected_valid_to: date | None,
) -> ParsedEonSupplierTariff:
    """Parse one exact supplier-commercial E.ON period and reject ambiguity."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("E.ON tariff text must not be empty")
    if not isinstance(expected_product_name, str) or not expected_product_name.strip():
        raise ValueError("expected_product_name must not be empty")
    if not isinstance(expected_distribution_tariff, str) or not expected_distribution_tariff.strip():
        raise ValueError("expected_distribution_tariff must not be empty")
    if not isinstance(expected_valid_from, date):
        raise ValueError("expected_valid_from must be a date")
    if expected_valid_to is not None and not isinstance(expected_valid_to, date):
        raise ValueError("expected_valid_to must be a date when provided")

    product_name = expected_product_name.strip()
    tariff = expected_distribution_tariff.strip()
    semantic_period_index = _period_index(
        product_name,
        expected_valid_from,
        expected_valid_to,
    )

    folded = _fold(text)
    if _fold(f"Ceník {product_name}") not in folded and _fold(f"Produktová řada {product_name}") not in folded:
        raise ValueError("E.ON tariff document product marker does not match expected product")
    if _fold(_COMMERCIAL_MARKER) not in folded:
        raise ValueError("E.ON tariff document is missing supplier-commercial marker")
    if _fold(_VAT_MARKER) not in folded:
        raise ValueError("E.ON tariff document is missing explicit 21% VAT convention")

    validity_match = _OVERALL_VALID_FROM_RE.search(text.replace("\u00a0", " ").replace("\u202f", " "))
    if validity_match is None:
        raise ValueError("E.ON tariff document is missing commercial validity marker")
    document_valid_from = _parse_date_match(validity_match)
    if product_name not in _EON_DOCUMENT_VALID_FROM:
        raise LookupError(f"unsupported E.ON product parser: {product_name}")
    if document_valid_from != _EON_DOCUMENT_VALID_FROM[product_name]:
        raise ValueError("E.ON tariff document commercial start does not match verified catalog")

    regulator_pos = text.find(_REGULATED_MARKER)
    commercial_text = text if regulator_pos < 0 else text[:regulator_pos]
    if _fold(_COMMERCIAL_MARKER) not in _fold(commercial_text):
        raise ValueError("E.ON supplier-commercial table is not before the regulated section")

    segment = _target_rate_segment(commercial_text, tariff)
    blocks = _all_price_blocks(
        segment,
        block_count=_EON_DOCUMENT_PRICE_BLOCKS[product_name],
    )
    selected = _select_semantic_price_block(
        product_name,
        semantic_period_index,
        blocks,
    )

    return ParsedEonSupplierTariff(
        product_name=product_name,
        valid_from=expected_valid_from,
        valid_to=expected_valid_to,
        distribution_tariff=tariff,
        high_rate_czk_per_kwh=selected.high_rate_czk_mwh / _MWH_TO_KWH,
        low_rate_czk_per_kwh=(
            None
            if selected.low_rate_czk_mwh is None
            else selected.low_rate_czk_mwh / _MWH_TO_KWH
        ),
        supplier_standing_czk_month=selected.standing_czk_month,
        includes_vat=True,
    )
