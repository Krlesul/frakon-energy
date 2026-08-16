"""Strict parser for official MND household electricity price-list PDFs.

The MND PDF contains both supplier-commercial prices and, on following pages,
regulated prices.  This parser deliberately reads only the first commercial
comparison table and never derives regulator authority from the supplier PDF.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
import unicodedata

_RATE_RE = re.compile(r"^D\d{2}d$", re.IGNORECASE)
_VALID_FROM_RE = re.compile(
    r"platn[eé]\s+od\s+(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})",
    re.IGNORECASE,
)
_GROSS_NET_RE = re.compile(
    r"(?<![\d,])"
    r"(\d{1,3}(?:[ \u00a0\u202f]\d{3})*|\d+)"
    r"\s*\["
    r"(\d{1,3}(?:[ \u00a0\u202f]\d{3})*|\d+),([0-9]{2})"
    r"\]"
)

_DISTRIBUTOR_PATTERNS = {
    "cez_distribuce": ("cez distribuce",),
    "eg_d": ("eg.d", "eg d", "egd"),
    "pre_distribuce": ("predistribuce", "pre distribuce"),
}

# The first-page MND commercial table groups D01d/D02d and D25d/D26d into
# common supplier-commercial rate rows.  Every dual-rate row has exactly four
# gross/net pairs: current-product VT, comparison VT, current-product NT,
# comparison NT.  D01d/D02d are single-rate and their row additionally carries
# the current/comparison standing charges.
_RATE_GROUPS = (
    (("D01d", "D02d"), "D01dD02d", False),
    (("D25d", "D26d"), "D25dD26d", True),
    (("D27d",), "D27d", True),
    (("D35d",), "D35d", True),
    (("D45d",), "D45d", True),
    (("D56d",), "D56d", True),
    (("D57d",), "D57d", True),
    (("D61d",), "D61d", True),
)


@dataclass(frozen=True, slots=True)
class ParsedMndSupplierTariff:
    """One exact MND supplier-commercial tariff row with VAT included."""

    product_name: str
    distribution_tariff: str
    distributor: str
    high_rate_czk_per_kwh: Decimal
    low_rate_czk_per_kwh: Decimal | None
    supplier_standing_czk_month: Decimal
    valid_from: date
    valid_to: date | None
    includes_vat: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.product_name, str) or not self.product_name.strip():
            raise ValueError("product_name must not be empty")
        if not _RATE_RE.fullmatch(self.distribution_tariff):
            raise ValueError("distribution_tariff must use a code such as D25d")
        if self.distributor not in _DISTRIBUTOR_PATTERNS:
            raise ValueError("unsupported MND distributor identity")
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
        if not isinstance(self.valid_from, date):
            raise ValueError("valid_from must be a date")
        if self.valid_to is not None:
            if not isinstance(self.valid_to, date):
                raise ValueError("valid_to must be a date")
            if self.valid_to < self.valid_from:
                raise ValueError("valid_to must not precede valid_from")
        if self.includes_vat is not True:
            raise ValueError("MND parser accepts only VAT-included commercial values")


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _identity(value: str) -> str:
    folded = _fold(value).replace("–", "-").replace("—", "-")
    return "".join(char for char in folded if char.isalnum())


def _normalize_tariff(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("distribution_tariff is required")
    raw = value.strip()
    tariff = raw[0].upper() + raw[1:-1] + raw[-1].lower()
    if not _RATE_RE.fullmatch(tariff):
        raise ValueError("distribution_tariff must use a code such as D25d")
    return tariff


def _normalize_distributor(value: str) -> str:
    if value not in _DISTRIBUTOR_PATTERNS:
        raise ValueError(f"unsupported distributor for MND parser: {value}")
    return value


def mnd_automatic_parser_supports_product(product_name: str) -> bool:
    """Return whether the verified single-period MND table layout is supported.

    MND's decreasing-price product is intentionally excluded until its own
    multi-period PDF layout has an exact frozen fixture and period mapping.
    """

    if not isinstance(product_name, str) or not product_name.strip():
        return False
    normalized = _identity(product_name)
    if "klesajici" in normalized:
        return False
    return normalized.startswith("proud")


def _extract_title(lines: tuple[str, ...]) -> str:
    for line in lines:
        folded = _fold(line).strip()
        if not folded.startswith("produkt "):
            continue
        title = line.strip()[len("Produkt ") :].strip()
        title = re.sub(r"\s+-\s+Domácnosti\s*$", "", title, flags=re.IGNORECASE)
        if title:
            return title
    raise ValueError("MND household product title was not found")


def _product_matches(document_title: str, expected_product_name: str) -> bool:
    return _identity(document_title) == _identity(expected_product_name)


def _extract_valid_from(text: str) -> date:
    match = _VALID_FROM_RE.search(_fold(text))
    if match is None:
        raise ValueError("MND commercial validity start was not found")
    day_value, month_value, year_value = (int(item) for item in match.groups())
    try:
        return date(year_value, month_value, day_value)
    except ValueError as err:
        raise ValueError("MND commercial validity start is invalid") from err


def _date_tokens(value: date) -> tuple[str, ...]:
    return (
        f"{value.day}. {value.month}. {value.year}",
        f"{value.day}.{value.month}.{value.year}",
        f"{value.day:02d}. {value.month:02d}. {value.year}",
    )


def _extract_distributor(text: str) -> str:
    folded = _fold(text)
    anchor = "v distribucni oblasti"
    index = folded.find(anchor)
    if index < 0:
        raise ValueError("MND distribution-area marker was not found")
    area = folded[index + len(anchor) : index + len(anchor) + 80]
    matches = [
        distributor
        for distributor, patterns in _DISTRIBUTOR_PATTERNS.items()
        if any(pattern in area for pattern in patterns)
    ]
    if len(matches) != 1:
        raise ValueError("MND distribution area could not be identified exactly")
    return matches[0]


def _commercial_table(text: str) -> str:
    folded = _fold(text)
    start = folded.find("distribucni\nsazba")
    if start < 0:
        start = folded.find("distribucni sazba")
    if start < 0:
        raise ValueError("MND first-page commercial table was not found")

    appendix_markers = (
        "priloha ceniku",
        "ceny s dph [bez dph]priloha ceniku",
    )
    ends = [folded.find(marker, start + 1) for marker in appendix_markers]
    ends = [value for value in ends if value >= 0]
    if not ends:
        raise ValueError("MND first-page commercial table boundary was not found")
    end = min(ends)
    if end <= start:
        raise ValueError("MND commercial table boundary is invalid")

    regulated = folded.find("ceny a sazby regulovane", end)
    if regulated < 0:
        raise ValueError("MND regulated-section boundary was not found")
    return text[start:end]


def _czech_decimal(integer_part: str, cents: str | None = None) -> Decimal:
    normalized = (
        integer_part.replace(" ", "")
        .replace("\u00a0", "")
        .replace("\u202f", "")
    )
    if cents is not None:
        normalized = f"{normalized}.{cents}"
    try:
        value = Decimal(normalized)
    except InvalidOperation as err:
        raise ValueError("invalid MND Czech decimal") from err
    if not value.is_finite() or value < 0:
        raise ValueError("MND prices must be finite and non-negative")
    return value


def _gross_net_pairs(section: str) -> tuple[tuple[Decimal, Decimal], ...]:
    pairs = tuple(
        (
            _czech_decimal(match.group(1)),
            _czech_decimal(match.group(2), match.group(3)),
        )
        for match in _GROSS_NET_RE.finditer(section)
    )
    return pairs


def _validate_vat_pair(gross: Decimal, net: Decimal) -> None:
    expected = (net * Decimal("1.21")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if abs(gross - expected) > Decimal("0.02"):
        raise ValueError(
            f"MND gross/net VAT pair is inconsistent: gross={gross}, net={net}"
        )


def _rate_sections(table: str) -> dict[str, str]:
    positions: list[tuple[int, str, tuple[str, ...], bool]] = []
    compact = table.replace(" ", "")
    # Positions are taken from the original table with a whitespace-tolerant
    # marker search so line breaks between paired tariff codes are harmless.
    for tariffs, marker, dual_rate in _RATE_GROUPS:
        pattern = re.compile("".join(re.escape(char) + r"\s*" for char in marker), re.IGNORECASE)
        match = pattern.search(table)
        if match is None:
            raise ValueError(f"MND commercial tariff group is missing: {marker}")
        positions.append((match.start(), marker, tariffs, dual_rate))
    positions.sort(key=lambda item: item[0])

    sections: dict[str, str] = {}
    for index, (start, _marker, tariffs, _dual_rate) in enumerate(positions):
        end = positions[index + 1][0] if index + 1 < len(positions) else len(table)
        section = table[start:end]
        for tariff in tariffs:
            sections[tariff] = section
    return sections


def _standing_price(table: str) -> Decimal:
    sections = _rate_sections(table)
    pairs = _gross_net_pairs(sections["D01d"])
    if len(pairs) != 4:
        raise ValueError("MND Standard row must contain current/comparison VT and standing pairs")
    for gross, net in pairs:
        _validate_vat_pair(gross, net)
    # Current product: pair 0 = VT, pair 1 = comparison VT,
    # pair 2 = standing, pair 3 = comparison standing.
    return pairs[2][0]


def _rate_prices(table: str, tariff: str) -> tuple[Decimal, Decimal | None]:
    section = _rate_sections(table)[tariff]
    pairs = _gross_net_pairs(section)
    expected_pair_count = 4
    if len(pairs) != expected_pair_count:
        raise ValueError(
            f"MND tariff row {tariff} must contain exactly four current/comparison VAT pairs"
        )
    for gross, net in pairs:
        _validate_vat_pair(gross, net)
    high_mwh = pairs[0][0]
    if tariff in {"D01d", "D02d"}:
        return high_mwh / Decimal("1000"), None
    low_mwh = pairs[2][0]
    return high_mwh / Decimal("1000"), low_mwh / Decimal("1000")


def parse_mnd_supplier_tariff(
    text: str,
    *,
    expected_product_name: str,
    expected_distribution_tariff: str,
    expected_distributor: str,
    expected_valid_from: date,
    expected_valid_to: date | None,
) -> ParsedMndSupplierTariff:
    """Parse exact supplier-commercial MND prices from a verified PDF text.

    The parser accepts the normal one-period MND household comparison-table
    layout.  It intentionally rejects decreasing/multi-period products until a
    separate exact period parser exists.
    """

    if not isinstance(text, str) or not text.strip():
        raise ValueError("MND price-list text must not be empty")
    if not isinstance(expected_product_name, str) or not expected_product_name.strip():
        raise ValueError("expected_product_name must not be empty")
    if not mnd_automatic_parser_supports_product(expected_product_name):
        raise LookupError(
            f"automatic MND parser does not support product: {expected_product_name}"
        )
    tariff = _normalize_tariff(expected_distribution_tariff)
    distributor = _normalize_distributor(expected_distributor)
    if not isinstance(expected_valid_from, date):
        raise ValueError("expected_valid_from must be a date")
    if expected_valid_to is not None:
        if not isinstance(expected_valid_to, date):
            raise ValueError("expected_valid_to must be a date or None")
        if expected_valid_to < expected_valid_from:
            raise ValueError("expected_valid_to must not precede expected_valid_from")

    folded = _fold(text)
    if "ceny obchodni za elektrinu pro domacnosti s dph [bez dph]" not in folded:
        raise ValueError("document is not marked as an MND household commercial price list")
    if "ceny a sazby regulovane" not in folded:
        raise ValueError("MND regulated-section marker is missing")

    lines = tuple(line.strip() for line in text.splitlines() if line.strip())
    title = _extract_title(lines)
    if not _product_matches(title, expected_product_name):
        raise ValueError(
            f"MND product identity mismatch: expected {expected_product_name!r}, found {title!r}"
        )

    valid_from = _extract_valid_from(text)
    if valid_from != expected_valid_from:
        raise ValueError(
            "MND commercial validity start does not match selected candidate: "
            f"{valid_from.isoformat()} != {expected_valid_from.isoformat()}"
        )
    if expected_valid_to is not None:
        normalized_text = _fold(text)
        if not any(_fold(token) in normalized_text for token in _date_tokens(expected_valid_to)):
            raise ValueError(
                "MND fixed-price end date does not match selected candidate: "
                f"{expected_valid_to.isoformat()}"
            )

    document_distributor = _extract_distributor(text)
    if document_distributor != distributor:
        raise ValueError(
            "MND distribution area does not match selected contract: "
            f"{document_distributor} != {distributor}"
        )

    table = _commercial_table(text)
    standing = _standing_price(table)
    high_rate, low_rate = _rate_prices(table, tariff)

    return ParsedMndSupplierTariff(
        product_name=expected_product_name.strip(),
        distribution_tariff=tariff,
        distributor=distributor,
        high_rate_czk_per_kwh=high_rate,
        low_rate_czk_per_kwh=low_rate,
        supplier_standing_czk_month=standing,
        valid_from=valid_from,
        valid_to=expected_valid_to,
    )
