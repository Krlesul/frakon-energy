from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types


EON_2026_PRE_TEXT = """
Elektřina
Ceník Elektřina výhodně PRO na 3 roky 6/26
pro domácnosti
Informace o ceníku
Produktová řada Elektřina výhodně PRO na 3 roky
Smlouva na 3 roky s fixací ceny
Obchodní cena za elektřinu platná od 17. 6. 2026
Obchodní cena za dodávku elektřiny Tučně uvedené ceny jsou včetně 21% DPH.

Elektřina
Ceník Elektřina výhodně PRO na 3 roky 6/26
Informace o ceníku
Ceník elektřiny pro domácnosti
Produktová řada Elektřina výhodně PRO na 3 roky
Obchodní cena za elektřinu platná od 17. 6. 2026 a regulované ceny pro rok 2026.
Distribuční území
PREdistribuce, a.s.
Tučně uvedené ceny jsou včetně 21% DPH.
Obchodní cena za dodávku elektřiny pro rok 2026
Distribuční sazba Klasik Aku Kombi Přímotop Víkend
D01d D02d D25d D26d D27d D35d D45d D56d D57d D61d ř.
Cena ve vysokém tarifu (VT) Kč/MWh
2 096,93
1 733,00
2 191,31
1 811,00
2 205,83
1 823,00
2 202,20
1 820,00
2 198,57
1 817,00 01
Cena v nízkém tarifu (NT) Kč/MWh
–
–
1 972,30
1 630,00
2 044,90
1 690,00
2 086,04
1 724,00
1 971,09
1 629,00 02
Stálý měsíční plat Kč/měsíc
168,19
139,00 03
Regulovaná cena za související služby v elektroenergetice
Cena za distribuci
Cena ve vysokém tarifu (VT) Kč/MWh
999 999,99
826 446,27
Cena v nízkém tarifu (NT) Kč/MWh
888 888,88
734 618,91
Celková jednotková cena elektřiny pro rok 2026
777 777,77
642 791,55
""".strip()


def load_module():
    for name in (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.providers",
        "custom_components.frakon_energy.providers.eon_tariff_parser",
    ):
        sys.modules.pop(name, None)

    for name in (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.providers",
    ):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    spec = importlib.util.spec_from_file_location(
        "custom_components.frakon_energy.providers.eon_tariff_parser",
        Path("custom_components/frakon_energy/providers/eon_tariff_parser.py"),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_d25d_parses_exact_commercial_gross_rows_from_official_layout() -> None:
    parser = load_module()

    result = parser.parse_eon_commercial_price_text(
        EON_2026_PRE_TEXT,
        distribution_tariff="d25D",
    )

    assert result.product_name == "Elektřina výhodně PRO na 3 roky"
    assert result.valid_from == date(2026, 6, 17)
    assert result.price_year == 2026
    assert result.distribution_tariff == "D25d"
    assert result.high_rate_czk_per_kwh == Decimal("2.19131")
    assert result.low_rate_czk_per_kwh == Decimal("1.97230")
    assert result.supplier_standing_czk_month == Decimal("168.19")
    assert result.includes_vat is True


def test_single_rate_d02d_keeps_low_rate_missing() -> None:
    parser = load_module()

    result = parser.parse_eon_commercial_price_text(
        EON_2026_PRE_TEXT,
        distribution_tariff="D02d",
    )

    assert result.high_rate_czk_per_kwh == Decimal("2.09693")
    assert result.low_rate_czk_per_kwh is None
    assert result.supplier_standing_czk_month == Decimal("168.19")


def test_d57d_uses_explicit_primotop_group_not_adjacent_column() -> None:
    parser = load_module()

    result = parser.parse_eon_commercial_price_text(
        EON_2026_PRE_TEXT,
        distribution_tariff="D57d",
    )

    assert result.high_rate_czk_per_kwh == Decimal("2.20220")
    assert result.low_rate_czk_per_kwh == Decimal("2.08604")


def test_regulated_and_total_rows_after_boundary_cannot_leak_into_supplier_price() -> None:
    parser = load_module()

    result = parser.parse_eon_commercial_price_text(
        EON_2026_PRE_TEXT,
        distribution_tariff="D25d",
    )

    assert result.high_rate_czk_per_kwh != Decimal("999.99999")
    assert result.low_rate_czk_per_kwh != Decimal("888.88888")
    assert result.high_rate_czk_per_kwh == Decimal("2.19131")
    assert result.low_rate_czk_per_kwh == Decimal("1.97230")


def test_unsupported_distribution_tariff_fails_closed() -> None:
    parser = load_module()

    try:
        parser.parse_eon_commercial_price_text(
            EON_2026_PRE_TEXT,
            distribution_tariff="D03d",
        )
    except ValueError as err:
        assert "unsupported E.ON distribution tariff" in str(err)
    else:
        raise AssertionError("Unknown E.ON tariff must never fall back to a price group")


def test_non_2026_detail_request_fails_closed_before_reusing_2026_rows() -> None:
    parser = load_module()

    try:
        parser.parse_eon_commercial_price_text(
            EON_2026_PRE_TEXT,
            distribution_tariff="D25d",
            price_year=2027,
        )
    except ValueError as err:
        assert "supports price year 2026 only" in str(err)
    else:
        raise AssertionError("2026 detailed rows must never be reused as 2027 pricing")


def test_missing_vat_marker_is_rejected() -> None:
    parser = load_module()
    text = EON_2026_PRE_TEXT.replace(
        "Tučně uvedené ceny jsou včetně 21% DPH.",
        "Ceny bez informace o DPH.",
    )

    try:
        parser.parse_eon_commercial_price_text(text, distribution_tariff="D25d")
    except ValueError as err:
        assert "VAT marker" in str(err)
    else:
        raise AssertionError("E.ON rows without explicit 21% VAT semantics must fail")


def test_missing_regulated_boundary_is_rejected() -> None:
    parser = load_module()
    text = EON_2026_PRE_TEXT.replace(
        "Regulovaná cena za související služby v elektroenergetice",
        "Regulovaný blok bez očekávaného nadpisu",
    )

    try:
        parser.parse_eon_commercial_price_text(text, distribution_tariff="D25d")
    except ValueError as err:
        assert "commercial boundary" in str(err)
    else:
        raise AssertionError("Parser must prove where supplier authority ends")


def test_corrupted_gross_net_pair_is_rejected() -> None:
    parser = load_module()
    text = EON_2026_PRE_TEXT.replace("1 811,00", "1 800,00", 1)

    try:
        parser.parse_eon_commercial_price_text(text, distribution_tariff="D25d")
    except ValueError as err:
        assert "VAT pair is inconsistent" in str(err)
    else:
        raise AssertionError("Commercial gross/net pair corruption must fail closed")


def test_commercial_rows_require_exact_five_group_shape() -> None:
    parser = load_module()
    text = EON_2026_PRE_TEXT.replace("2 198,57\n1 817,00 01", "2 198,57 01")

    try:
        parser.parse_eon_commercial_price_text(text, distribution_tariff="D25d")
    except ValueError as err:
        assert "exactly 5 gross/net pairs" in str(err)
    else:
        raise AssertionError("Incomplete E.ON commercial row must fail closed")
