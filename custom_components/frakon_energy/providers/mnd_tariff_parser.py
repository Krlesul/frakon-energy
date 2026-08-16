"""Strict parser for official MND household electricity price-list PDFs.

MND price lists contain supplier-commercial prices and, on following pages,
regulated prices. This parser deliberately reads only the first commercial
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
_RATE_GROUPS = (
    (("D01d", "D02d"), "D01dD02d"),
    (("D25d", "D26d"), "D25dD26d"),
    (("D27d",), "D27d"),
    (("D35d",), "D35d"),
    (("D45d",), "D45d"),
    (("D56d",), "D56d"),
    (("D57d",), "D57d"),
    (("D61d",), "D61d"),
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
        for field_name in ("high_rate_czk_per_kwh", "supplier_standing_czk_month"):
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


def _space_fold(value: str) -> str:
    return re.sub(r"\s+", " ", _fold(value)).strip()


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
    """Return whether the verified single-period MND table layout is supported."""

    if not isinstance(product_name, str) or not product_name.strip():
        return False
    normalized = _identity(product_name)
    if "klesajici" in normalized:
        return False
    return normalized.startswith("proud")


def _extract_title(lines: tuple[str, ...]) -> str:
    for line in lines:
        if not _fold(line).strip().startswith("produkt "):
            continue
        title = line.strip()[len("Produkt ") :].strip()
        title = re.sub(r"\s+-\s+Domácnosti\s*$", "", title, flags=re.IGNORECASE)
        if title:
            return title
    raise ValueError("MND household product title was not found")


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
    folded = _space_fold(text)
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
    """Return only the first-page supplier-commercial comparison table."""

    lines = text.splitlines()
    folded_lines = [_space_fold(line) for line in lines]
    start: int | None = None
    for index, folded in enumerate(folded_lines):
        if folded == "distribucni sazba" or folded.startswith("distribucni sazba "):
            start = index
            break
        if (
            folded == "distribucni"
            and index + 1 < len(folded_lines)
            and folded_lines[index + 1] == "sazba"
        ):
            start = index
            break
    if start is None:
        raise ValueError("MND first-page commercial table was not found")

    end: int | None = None
    for index in range(start + 1, len(lines)):
        if "priloha ceniku" in folded_lines[index]:
            end = index
            break
    if end is None or end <= start:
        raise ValueError("MND first-page commercial table boundary was not found")

    if not any(
        "ceny a sazby regulovane" in folded
        for folded in folded_lines[end + 1 :]
    ):
        raise ValueError("MND regulated-section boundary was not found")
    return "\n".join(lines[start:end])


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
    return tuple(
        (
            _czech_decimal(match.group(1)),
            _czech_decimal(match.group(2), match.group(3)),
        )
        for match in _GROSS_NET_RE.finditer(section)
    )


def _validate_vat_pair(gross: Decimal, net: Decimal) -> None:
    expected = (net * Decimal("1.21")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    if abs(gross - expected) > Decimal("0.02"):
        raise ValueError(
            f"MND gross/net VAT pair is inconsistent: gross={gross}, net={net}"
        )


def _rate_sections(table: str) -> dict[str, str]:
    positions: list[tuple[int, tuple[str, ...], str]] = []
    for tariffs, marker in _RATE_GROUPS:
        pattern = re.compile(
            r"\s*".join(re.escape(char) for char in marker),
            re.IGNORECASE,
        )
        match = pattern.search(table)
        if match is None:
            raise ValueError(f"MND commercial tariff group is missing: {marker}")
        positions.append((match.start(), tariffs, marker))
    positions.sort(key=lambda item: item[0])

    sections: dict[str, str] = {}
    for index, (start, tariffs, _marker) in enumerate(positions):
        end = positions[index + 1][0] if index + 1 < len(positions) else len(table)
        section = table[start:end]
        for tariff in tariffs:
            sections[tariff] = section
    return sections


def _standing_price(table: str) -> Decimal:
    pairs = _gross_net_pairs(_rate_sections(table)["D01d"])
    if len(pairs) != 4:
        raise ValueError(
            "MND Standard row must contain current/comparison VT and standing pairs"
        )
    for gross, net in pairs:
        _validate_vat_pair(gross, net)
    # pair 0 = current VT, pair 1 = comparison VT,
    # pair 2 = current standing, pair 3 = comparison standing.
    return pairs[2][0]


def _rate_prices(table: str, tariff: str) -> tuple[Decimal, Decimal | None]:
    pairs = _gross_net_pairs(_rate_sections(table)[tariff])
    if len(pairs) != 4:
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

    The normal one-period MND household comparison-table layout is supported.
    Decreasing/multi-period products remain fail-closed until their own exact
    period fixture and mapping are implemented.
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

    folded = _space_fold(text)
    if "ceny obchodni za elektrinu pro domacnosti s dph [bez dph]" not in folded:
        raise ValueError("document is not marked as an MND household commercial price list")
    if "ceny a sazby regulovane" not in folded:
        raise ValueError("MND regulated-section marker is missing")

    lines = tuple(line.strip() for line in text.splitlines() if line.strip())
    title = _extract_title(lines)
    if _identity(title) != _identity(expected_product_name):
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
        if not any(_space_fold(token) in folded for token in _date_tokens(expected_valid_to)):
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
