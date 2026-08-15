from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types

import pytest


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_parser():
    for name in (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.providers",
        "custom_components.frakon_energy.providers.eon_tariffs",
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
    _load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    tariffs = _load(
        "custom_components.frakon_energy.providers.eon_tariffs",
        "custom_components/frakon_energy/providers/eon_tariffs.py",
    )
    parser = _load(
        "custom_components.frakon_energy.providers.eon_tariff_parser",
        "custom_components/frakon_energy/providers/eon_tariff_parser.py",
    )
    return tariffs, parser


VARIANT_TEXT = """
Elektřina
Ceník Variant PRO na 2 roky 3/26
Produktová řada Variant PRO na 2 roky
Obchodní cena za elektřinu platná od 30. 3. 2026
Obchodní cena za dodávku elektřiny
Tučně uvedené ceny jsou včetně 21% DPH.
Běžná spotřeba D01d, D02d
3 3322 754
–
–
168
139
Ohřev vody, akumulační vytápění, elektromobil D25d, D26d, D27d
3 3202 744
2 9872 469
168
139
Hybridní vytápění D35d
3 3202 744
2 9872 469
168
139
Přímotopné vytápění, tepelné čerpadlo D45d, D56d, D57d
3 3202 744
2 9872 469
168
139
Víkendová spotřeba D61d
3 3202 744
2 9872 469
168
139
Celková cena elektřiny zahrnuje obchodní cenu za dodávku elektřiny a cenu za související služby.
Regulovaná cena za související služby v elektroenergetice
D25d, D26d, D27d
99 999
88 888
77 777
66 666
55 555
44 444
"""


THREE_YEAR_TEXT = """
Elektřina
Ceník Elektřina výhodně PRO na 3 roky 6/26
Produktová řada Elektřina výhodně PRO na 3 roky
Obchodní cena za elektřinu platná od 17. 6. 2026
Obchodní cena za dodávku elektřiny
Tučně uvedené ceny jsou včetně 21% DPH.
Běžná spotřeba D01d, D02d
2 6652 202
–
–
168
139
3 3882 800
–
–
168
139
3 3882 800
–
–
168
139
3 3882 800
–
–
168
139
Ohřev vody, akumulační vytápění, elektromobil D25d, D26d, D27d
2 5382 098
2 3021 902
168
139
3 2612 695
2 9342 425
168
139
3 2612 695
2 9342 425
168
139
3 2612 695
2 9342 425
168
139
Hybridní vytápění D35d
2 5382 098
2 3021 902
168
139
3 2612 695
2 9342 425
168
139
3 2612 695
2 9342 425
168
139
3 2612 695
2 9342 425
168
139
Přímotopné vytápění, tepelné čerpadlo D45d, D56d, D57d
2 5382 098
2 3021 902
168
139
3 2612 695
2 9342 425
168
139
3 2612 695
2 9342 425
168
139
3 2612 695
2 9342 425
168
139
Víkendová spotřeba D61d
2 5382 098
2 3021 902
168
139
3 2612 695
2 9342 425
168
139
3 2612 695
2 9342 425
168
139
3 2612 695
2 9342 425
168
139
Celková cena elektřiny zahrnuje obchodní cenu za dodávku elektřiny a cenu za související služby.
Regulovaná cena za související služby v elektroenergetice
D25d, D26d, D27d
90 000
80 000
70 000
60 000
50 000
40 000
"""


def test_variant_d25_parses_gross_commercial_values_and_ignores_regulated_rows() -> None:
    _tariffs, parser = load_parser()
    parsed = parser.parse_eon_supplier_tariff(
        VARIANT_TEXT,
        expected_product_name="Variant PRO na 2 roky",
        expected_distribution_tariff="D25d",
        expected_valid_from=date(2026, 3, 30),
        expected_valid_to=None,
    )

    assert parsed.product_name == "Variant PRO na 2 roky"
    assert parsed.high_rate_czk_per_kwh == Decimal("3.320")
    assert parsed.low_rate_czk_per_kwh == Decimal("2.987")
    assert parsed.supplier_standing_czk_month == Decimal("168")
    assert parsed.includes_vat is True


def test_variant_single_rate_keeps_nt_absent() -> None:
    _tariffs, parser = load_parser()
    parsed = parser.parse_eon_supplier_tariff(
        VARIANT_TEXT,
        expected_product_name="Variant PRO na 2 roky",
        expected_distribution_tariff="D02d",
        expected_valid_from=date(2026, 3, 30),
        expected_valid_to=None,
    )

    assert parsed.high_rate_czk_per_kwh == Decimal("3.332")
    assert parsed.low_rate_czk_per_kwh is None
    assert parsed.supplier_standing_czk_month == Decimal("168")


def test_three_year_2026_selects_promotional_block() -> None:
    _tariffs, parser = load_parser()
    parsed = parser.parse_eon_supplier_tariff(
        THREE_YEAR_TEXT,
        expected_product_name="Elektřina výhodně PRO na 3 roky",
        expected_distribution_tariff="D25d",
        expected_valid_from=date(2026, 6, 17),
        expected_valid_to=date(2026, 12, 31),
    )

    assert parsed.high_rate_czk_per_kwh == Decimal("2.538")
    assert parsed.low_rate_czk_per_kwh == Decimal("2.302")
    assert parsed.supplier_standing_czk_month == Decimal("168")


def test_three_year_2027_plus_selects_fixed_future_block_only_after_repeated_columns_agree() -> None:
    _tariffs, parser = load_parser()
    parsed = parser.parse_eon_supplier_tariff(
        THREE_YEAR_TEXT,
        expected_product_name="Elektřina výhodně PRO na 3 roky",
        expected_distribution_tariff="D25d",
        expected_valid_from=date(2027, 1, 1),
        expected_valid_to=None,
    )

    assert parsed.high_rate_czk_per_kwh == Decimal("3.261")
    assert parsed.low_rate_czk_per_kwh == Decimal("2.934")
    assert parsed.supplier_standing_czk_month == Decimal("168")


def test_three_year_future_column_disagreement_fails_closed() -> None:
    _tariffs, parser = load_parser()
    tampered = THREE_YEAR_TEXT.replace(
        "3 2612 695\n2 9342 425\n168\n139\n3 2612 695\n2 9342 425\n168\n139\n3 26122 695",
        "3 2612 695\n2 9342 425\n168\n139\n3 2622 696\n2 9342 425\n168\n139\n3 2612 695",
        1,
    )
    # Use a direct target-row replacement that cannot be absorbed by regulator data.
    tampered = tampered.replace(
        "3 2612 695\n2 9342 425\n168\n139\n3 2612 695\n2 9342 425\n168\n139\n3 2612 695\n2 9342 425\n168\n139\nHybridní vytápění",
        "3 2612 695\n2 9342 425\n168\n139\n3 2622 696\n2 9342 425\n168\n139\n3 2612 695\n2 9342 425\n168\n139\nHybridní vytápění",
        1,
    )

    with pytest.raises(ValueError, match=r"fixed 2027\+ price columns disagree"):
        parser.parse_eon_supplier_tariff(
            tampered,
            expected_product_name="Elektřina výhodně PRO na 3 roky",
            expected_distribution_tariff="D25d",
            expected_valid_from=date(2027, 1, 1),
            expected_valid_to=None,
        )


def test_candidate_validity_must_match_verified_semantic_period() -> None:
    _tariffs, parser = load_parser()
    with pytest.raises(ValueError, match="candidate validity"):
        parser.parse_eon_supplier_tariff(
            THREE_YEAR_TEXT,
            expected_product_name="Elektřina výhodně PRO na 3 roky",
            expected_distribution_tariff="D25d",
            expected_valid_from=date(2028, 1, 1),
            expected_valid_to=None,
        )


def test_missing_commercial_or_vat_marker_fails_closed() -> None:
    _tariffs, parser = load_parser()
    without_commercial = VARIANT_TEXT.replace("Obchodní cena za dodávku elektřiny", "Cena elektřiny")
    with pytest.raises(ValueError, match="supplier-commercial marker"):
        parser.parse_eon_supplier_tariff(
            without_commercial,
            expected_product_name="Variant PRO na 2 roky",
            expected_distribution_tariff="D25d",
            expected_valid_from=date(2026, 3, 30),
            expected_valid_to=None,
        )

    without_vat = VARIANT_TEXT.replace("Tučně uvedené ceny jsou včetně 21% DPH.", "")
    with pytest.raises(ValueError, match="21% VAT convention"):
        parser.parse_eon_supplier_tariff(
            without_vat,
            expected_product_name="Variant PRO na 2 roky",
            expected_distribution_tariff="D25d",
            expected_valid_from=date(2026, 3, 30),
            expected_valid_to=None,
        )


def test_wrong_product_unknown_tariff_and_incomplete_matrix_fail_closed() -> None:
    _tariffs, parser = load_parser()
    with pytest.raises(ValueError, match="product marker"):
        parser.parse_eon_supplier_tariff(
            VARIANT_TEXT,
            expected_product_name="Elektřina výhodně PRO na 3 roky",
            expected_distribution_tariff="D25d",
            expected_valid_from=date(2026, 6, 17),
            expected_valid_to=date(2026, 12, 31),
        )

    with pytest.raises(LookupError, match="distribution tariff"):
        parser.parse_eon_supplier_tariff(
            VARIANT_TEXT,
            expected_product_name="Variant PRO na 2 roky",
            expected_distribution_tariff="D99d",
            expected_valid_from=date(2026, 3, 30),
            expected_valid_to=None,
        )

    incomplete = VARIANT_TEXT.replace("2 9872 469\n168\n139\nHybridní", "2 9872 469\n168\nHybridní", 1)
    with pytest.raises(ValueError, match="exact expected price matrix"):
        parser.parse_eon_supplier_tariff(
            incomplete,
            expected_product_name="Variant PRO na 2 roky",
            expected_distribution_tariff="D25d",
            expected_valid_from=date(2026, 3, 30),
            expected_valid_to=None,
        )
