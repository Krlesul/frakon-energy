"""Pure parser for supplier-commercial rows in verified E.ON household PDFs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re

_RATE_RE = re.compile(r"^D\d{2}d$")
_NUMBER_RE = re.compile(
    r"(?<!\d)(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})*|\d+),\d{2}(?!\d)|[–—-]"
)
_VALID_FROM_RE = re.compile(
    r"Obchodní\s+cena\s+za\s+elektřinu\s+platná\s+od\s+"
    r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})",
    re.IGNORECASE,
)
_PRODUCT_RE = re.compile(
    r"^[ \t]*Produktová[ \t]+řada[ \t]+(.+?)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)

_COMMERCIAL_MARKER = "Obchodní cena za dodávku elektřiny pro rok 2026"
_REGULATED_MARKER = "Regulovaná cena za související služby v elektroenergetice"
_VT_MARKER = "Cena ve vysokém tarifu (VT) Kč/MWh"
_NT_MARKER = "Cena v nízkém tarifu (NT) Kč/MWh"
_STANDING_MARKER = "Stálý měsíční plat Kč/měsíc"
_VAT_MARKER = "Tučně uvedené ceny jsou včetně 21% DPH."

_RATE_GROUP: dict[str, int] = {
    "D01d": 0,
    "D02d": 0,
    "D25d": 1,
    "D26d": 1,
    "D27d": 1,
    "D35d": 2,
    "D45d": 3,
    "D56d": 3,
    "D57d": 3,
    "D61d": 4,
}


@dataclass(frozen=True, slots=True)
class ParsedEonCommercialPrice:
    """One distribution-tariff view of E.ON supplier-commercial 2026 prices."""

    product_name: str
    valid_from: date
    price_year: int
    distribution_tariff: str
    high_rate_czk_per_kwh: Decimal
    low_rate_czk_per_kwh: Decimal | None
    supplier_standing_czk_month: Decimal
    includes_vat: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.product_name, str) or not self.product_name.strip():
            raise ValueError("product_name must not be empty")
        if self.price_year != 2026:
            raise ValueError("E.ON commercial parser currently supports price year 2026 only")
        if self.distribution_tariff not in _RATE_GROUP:
            raise ValueError("unsupported E.ON distribution tariff")
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
            raise ValueError("E.ON commercial parser accepts only VAT-included prices")


def _compact(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").replace("\u202f", " ").split())


def _normalize_tariff(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("distribution_tariff is required")
    tariff = value.strip()
    tariff = tariff[0].upper() + tariff[1:-1] + tariff[-1].lower()
    if not _RATE_RE.fullmatch(tariff) or tariff not in _RATE_GROUP:
        raise ValueError(f"unsupported E.ON distribution tariff: {value}")
    return tariff


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


def _slice_between(text: str, start: str, end: str) -> str:
    start_index = text.casefold().find(start.casefold())
    if start_index < 0:
        raise ValueError(f"E.ON commercial marker was not found: {start}")
    start_index += len(start)
    end_index = text.casefold().find(end.casefold(), start_index)
    if end_index < 0:
        raise ValueError(f"E.ON commercial boundary was not found: {end}")
    return text[start_index:end_index]


def _row_tokens(text: str, start: str, end: str | None = None) -> tuple[str, ...]:
    start_index = text.casefold().find(start.casefold())
    if start_index < 0:
        raise ValueError(f"E.ON commercial row was not found: {start}")
    start_index += len(start)
    if end is None:
        row = text[start_index:]
    else:
        end_index = text.casefold().find(end.casefold(), start_index)
        if end_index < 0:
            raise ValueError(f"E.ON commercial row boundary was not found: {end}")
        row = text[start_index:end_index]
    return tuple(match.group(0) for match in _NUMBER_RE.finditer(row))


def _validated_vat_pair(
    gross_token: str,
    net_token: str,
    *,
    field: str,
    allow_missing: bool = False,
) -> Decimal | None:
    gross = _parse_czech_decimal(gross_token)
    net = _parse_czech_decimal(net_token)
    if gross is None or net is None:
        if allow_missing and gross is None and net is None:
            return None
        raise ValueError(f"E.ON {field} gross/net pair is incomplete")

    expected = net * Decimal("1.21")
    if abs(gross - expected) > Decimal("0.02"):
        raise ValueError(f"E.ON {field} gross/net VAT pair is inconsistent")
    return gross


def _extract_product(text: str) -> str:
    match = _PRODUCT_RE.search(text)
    if match is None:
        raise ValueError("E.ON product line was not found")
    product = match.group(1).strip()
    if not product:
        raise ValueError("E.ON product name must not be empty")
    return product


def _extract_valid_from(compact: str) -> date:
    match = _VALID_FROM_RE.search(compact)
    if match is None:
        raise ValueError("E.ON commercial validity date was not found")
    day, month, year = (int(item) for item in match.groups())
    try:
        return date(year, month, day)
    except ValueError as err:
        raise ValueError("E.ON commercial validity date is invalid") from err


def parse_eon_commercial_price_text(
    text: str,
    *,
    distribution_tariff: str,
    price_year: int = 2026,
) -> ParsedEonCommercialPrice:
    """Parse exact 2026 supplier-commercial rows from extracted E.ON PDF text.

    The parser intentionally slices the document at the explicit regulated-price
    heading and validates gross/net VAT pairs. It never uses the document's
    regulated or total all-in rows as supplier pricing authority.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("price-list text must not be empty")
    if isinstance(price_year, bool) or not isinstance(price_year, int):
        raise ValueError("price_year must be an integer")
    if price_year != 2026:
        raise ValueError("E.ON commercial parser currently supports price year 2026 only")

    compact = _compact(text)
    if _compact(_VAT_MARKER).casefold() not in compact.casefold():
        raise ValueError("E.ON 21% VAT marker was not found")

    commercial = _slice_between(compact, _COMMERCIAL_MARKER, _REGULATED_MARKER)
    vt_tokens = _row_tokens(commercial, _VT_MARKER, _NT_MARKER)
    nt_tokens = _row_tokens(commercial, _NT_MARKER, _STANDING_MARKER)
    standing_tokens = _row_tokens(commercial, _STANDING_MARKER)

    if len(vt_tokens) != 10:
        raise ValueError("E.ON 2026 commercial VT row must contain exactly 5 gross/net pairs")
    if len(nt_tokens) != 10:
        raise ValueError("E.ON 2026 commercial NT row must contain exactly 5 gross/net pairs")
    if len(standing_tokens) != 2:
        raise ValueError("E.ON 2026 commercial standing row must contain exactly one gross/net pair")

    vt: list[Decimal] = []
    nt: list[Decimal | None] = []
    for index in range(5):
        vt_value = _validated_vat_pair(
            vt_tokens[index * 2],
            vt_tokens[index * 2 + 1],
            field=f"VT group {index + 1}",
        )
        if vt_value is None:
            raise ValueError("E.ON VT price must be numeric")
        vt.append(vt_value)
        nt.append(
            _validated_vat_pair(
                nt_tokens[index * 2],
                nt_tokens[index * 2 + 1],
                field=f"NT group {index + 1}",
                allow_missing=(index == 0),
            )
        )

    standing = _validated_vat_pair(
        standing_tokens[0],
        standing_tokens[1],
        field="standing charge",
    )
    if standing is None:
        raise ValueError("E.ON standing charge must be numeric")

    tariff = _normalize_tariff(distribution_tariff)
    group = _RATE_GROUP[tariff]
    return ParsedEonCommercialPrice(
        product_name=_extract_product(text),
        valid_from=_extract_valid_from(compact),
        price_year=price_year,
        distribution_tariff=tariff,
        high_rate_czk_per_kwh=vt[group] / Decimal("1000"),
        low_rate_czk_per_kwh=(
            nt[group] / Decimal("1000") if nt[group] is not None else None
        ),
        supplier_standing_czk_month=standing,
    )
