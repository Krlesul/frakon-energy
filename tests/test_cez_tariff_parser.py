from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys


BASIC_2026_TEXT = """
Ceník elektřiny pro domácnosti
Basic
Smlouva na dobu neurčitou
Účinnost obchodních cen od 1. 1. 2026
Distribuční sazba D01d D02d D25d D26d D27d D35d D45d D56d D57d D61d
Vysoký tarif Kč/MWh
Nízký tarif Kč/MWh
Stálá platba Kč/měsíc
Uvádíme jen obchodní (neregulovanou) část ceny.
Tučně uvedené ceny jsou s 21% DPH, v závorce bez DPH.
3 860,00 3 860,00 3 960,00 3 960,00 3 960,00 4 140,00 4 140,00 4 140,00 4 140,00 3 860,00
(3 190,08) (3 190,08) (3 272,73) (3 272,73) (3 272,73) (3 421,49) (3 421,49) (3 421,49) (3 421,49) (3 190,08)
– – 3 700,00 3 700,00 3 650,00 4 020,00 4 020,00 4 020,00 4 020,00 3 860,00
(3 057,85) (3 057,85) (3 016,53) (3 322,31) (3 322,31) (3 322,31) (3 322,31) (3 190,08)
147,62 147,62 130,68 130,68 130,68 130,68 130,68 130,68 130,68 130,68
(122,00) (122,00) (108,00) (108,00) (108,00) (108,00) (108,00) (108,00) (108,00) (108,00)
"""


def load_parser():
    path = Path("custom_components/frakon_energy/providers/cez_tariff_parser.py")
    spec = importlib.util.spec_from_file_location("frakon_energy_cez_tariff_parser", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_d25d_commercial_prices_with_vat() -> None:
    module = load_parser()

    result = module.parse_cez_commercial_price_text(
        BASIC_2026_TEXT,
        distribution_tariff="d25D",
    )

    assert result.product_name == "Basic"
    assert result.valid_from == date(2026, 1, 1)
    assert result.distribution_tariff == "D25d"
    assert result.high_rate_czk_per_kwh == Decimal("3.96000")
    assert result.low_rate_czk_per_kwh == Decimal("3.70000")
    assert result.supplier_standing_czk_month == Decimal("130.68")
    assert result.includes_vat is True


def test_parse_single_rate_tariff_preserves_missing_low_rate() -> None:
    module = load_parser()

    result = module.parse_cez_commercial_price_text(
        BASIC_2026_TEXT,
        distribution_tariff="D01d",
    )

    assert result.high_rate_czk_per_kwh == Decimal("3.86000")
    assert result.low_rate_czk_per_kwh is None
    assert result.supplier_standing_czk_month == Decimal("147.62")


def test_parser_rejects_document_without_supplier_commercial_scope_marker() -> None:
    module = load_parser()
    invalid = BASIC_2026_TEXT.replace(
        "Uvádíme jen obchodní (neregulovanou) část ceny.",
        "Toto je jiný typ dokumentu.",
    )

    try:
        module.parse_cez_commercial_price_text(invalid, distribution_tariff="D25d")
    except ValueError as err:
        assert "supplier-commercial" in str(err)
    else:
        raise AssertionError("Document without supplier-commercial marker must be rejected")


def test_parser_rejects_document_without_vat_convention() -> None:
    module = load_parser()
    invalid = BASIC_2026_TEXT.replace(
        "Tučně uvedené ceny jsou s 21% DPH, v závorce bez DPH.",
        "DPH není v tomto dokumentu určena.",
    )

    try:
        module.parse_cez_commercial_price_text(invalid, distribution_tariff="D25d")
    except ValueError as err:
        assert "VAT" in str(err)
    else:
        raise AssertionError("Document without explicit VAT convention must be rejected")


def test_parser_rejects_unknown_distribution_tariff() -> None:
    module = load_parser()

    try:
        module.parse_cez_commercial_price_text(
            BASIC_2026_TEXT,
            distribution_tariff="D99d",
        )
    except ValueError as err:
        assert "not present" in str(err)
    else:
        raise AssertionError("Unknown distribution tariff must be rejected")


def test_parser_rejects_incomplete_price_rows() -> None:
    module = load_parser()
    incomplete = BASIC_2026_TEXT.replace(
        "147,62 147,62 130,68 130,68 130,68 130,68 130,68 130,68 130,68 130,68",
        "147,62 147,62",
    )

    try:
        module.parse_cez_commercial_price_text(
            incomplete,
            distribution_tariff="D25d",
        )
    except ValueError as err:
        assert "rows were not found" in str(err)
    else:
        raise AssertionError("Incomplete commercial table must be rejected")
