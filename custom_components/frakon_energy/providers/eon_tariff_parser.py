"""Strict parser for E.ON household electricity supplier-commercial price lists."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re
import unicodedata

_VAT_RATE = Decimal("1.21")
_MWH_TO_KWH = Decimal("1000")
_REGULATED_MARKER = "Regulovaná cena za související služby v elektroenergetice"
_COMMERCIAL_MARKER = "Obchodní cena za dodávku elektřiny"
_VAT_MARKER = "Tučně uvedené ceny jsou včetně 21% DPH"
_OVERALL_VALID_FROM_RE = re.compile(
    r"Obchodní\s+cena\s+za\s+elektřinu\s+platná\s+od\s+(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})",
    re.IGNORECASE,
)
_PRICE_TOKEN_RE = re.compile(
    r"(?<!\d)(?:\d{1,3}(?:[\s\u00a0]\d{3})+|\d{1,4})(?:,\d{1,2})?(?!\d)|[–—]"
)

_RATE_GROUPS: tuple[tuple[str, ...], ...] = (
    ("D01d", "D02d"),
    ("D25d", "D26d", "D27d"),
    ("D35d",),
    ("D45d", "D56d", "D57d"),
    ("D61d",),
)


@dataclass(frozen=True, slots=True)
class EonCommercialPricePeriod:
    """One advertised price period inside a multi-period E.ON price list."""

    valid_from: date
    valid_to: date | None


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


EON_PRODUCT_PERIODS: dict[str, tuple[EonCommercialPricePeriod, ...]] = {
    "Variant PRO na 2 roky": (
        EonCommercialPricePeriod(date(2026, 3, 30), None),
    ),
    "Elektřina výhodně PRO na 3 roky": (
        EonCommercialPricePeriod(date(2026, 6, 17), date(2026, 12, 31)),
        EonCommercialPricePeriod(date(2027, 1, 1), date(2027, 12, 31)),
        EonCommercialPricePeriod(date(2028, 1, 1), date(2028, 12, 31)),
        EonCommercialPricePeriod(date(2029, 1, 1), None),
    ),
}

_EON_DOCUMENT_VALID_FROM = {
    "Variant PRO na 2 roky": date(2026, 3, 30),
    "Elektřina výhodně PRO na 3 roky": date(2026, 6, 17),
}


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(normalized.replace("\u00a0", " ").split())


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


def _price_or_dash(token: str) -> Decimal | None:
    if token in {"–", "—"}:
        return None
    normalized = token.replace("\u00a0", " ").replace(" ", "").replace(",", ".")
    try:
        value = Decimal(normalized)
    except InvalidOperation as err:
        raise ValueError(f"invalid E.ON price token: {token}") from err
    if value < 0:
        raise ValueError("E.ON commercial price cannot be negative")
    return value


def _validate_vat_pair(gross: Decimal, net: Decimal, *, field: str) -> None:
    expected = net * _VAT_RATE
    # E.ON page-one summary rounds displayed gross values to whole CZK while the
    # detailed current-year table can contain two decimals. Both are official;
    # allowing less than one CZK per MWh/month covers display rounding only.
    if abs(gross - expected) > Decimal("0.75"):
        raise ValueError(f"E.ON {field} gross/net pair is inconsistent with 21% VAT")


def _selected_period_values(
    segment: str,
    *,
    period_index: int,
    period_count: int,
) -> tuple[Decimal, Decimal | None, Decimal]:
    tokens = [match.group(0).strip() for match in _PRICE_TOKEN_RE.finditer(segment)]
    expected_tokens = period_count * 6
    if len(tokens) != expected_tokens:
        raise ValueError(
            "E.ON commercial tariff row does not contain the exact expected price matrix"
        )

    offset = period_index * 6
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
    return gross_vt, gross_nt, gross_fixed


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
    period_index = _period_index(product_name, expected_valid_from, expected_valid_to)
    periods = EON_PRODUCT_PERIODS[product_name]

    folded = _fold(text)
    if _fold(f"Ceník {product_name}") not in folded and _fold(f"Produktová řada {product_name}") not in folded:
        raise ValueError("E.ON tariff document product marker does not match expected product")
    if _fold(_COMMERCIAL_MARKER) not in folded:
        raise ValueError("E.ON tariff document is missing supplier-commercial marker")
    if _fold(_VAT_MARKER) not in folded:
        raise ValueError("E.ON tariff document is missing explicit 21% VAT convention")

    validity_match = _OVERALL_VALID_FROM_RE.search(text.replace("\u00a0", " "))
    if validity_match is None:
        raise ValueError("E.ON tariff document is missing commercial validity marker")
    document_valid_from = _parse_date_match(validity_match)
    if document_valid_from != _EON_DOCUMENT_VALID_FROM[product_name]:
        raise ValueError("E.ON tariff document commercial start does not match verified catalog")

    regulator_pos = text.find(_REGULATED_MARKER)
    commercial_text = text if regulator_pos < 0 else text[:regulator_pos]
    if _fold(_COMMERCIAL_MARKER) not in _fold(commercial_text):
        raise ValueError("E.ON supplier-commercial table is not before the regulated section")

    segment = _target_rate_segment(commercial_text, tariff)
    gross_vt_mwh, gross_nt_mwh, gross_fixed_month = _selected_period_values(
        segment,
        period_index=period_index,
        period_count=len(periods),
    )

    return ParsedEonSupplierTariff(
        product_name=product_name,
        valid_from=expected_valid_from,
        valid_to=expected_valid_to,
        distribution_tariff=tariff,
        high_rate_czk_per_kwh=gross_vt_mwh / _MWH_TO_KWH,
        low_rate_czk_per_kwh=(
            None if gross_nt_mwh is None else gross_nt_mwh / _MWH_TO_KWH
        ),
        supplier_standing_czk_month=gross_fixed_month,
        includes_vat=True,
    )
