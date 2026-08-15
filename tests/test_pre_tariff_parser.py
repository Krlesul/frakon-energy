from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys

import pytest


NEFIX_TEXT = """
Ceník elektřiny pro domácnosti platný od 1. 1. 2026
na distribučním území PREdistribuce, a. s.
PRE PROUD neFIX
Distribuční sazba
D01d, D02d D25d, D26d D27d D35d D45d D56d D57d D61d
Cena za spotřebovanou elektřinu
ve vysokém tarifu [Kč/MWh]
v nízkém tarifu [Kč/MWh]
Měsíční plat za odběrné místo
[Kč/měsíc]
4 356,00
(3 600,00)
4 235,00
(3 500,00)
4 235,00
(3 500,00)
4 356,00
(3 600,00)
4 598,00
(3 800,00)
4 598,00
(3 800,00)
4 598,00
(3 800,00)
4 235,00
(3 500,00)
— 3 993,00
(3 300,00)
3 993,00
(3 300,00)
4 114,00
(3 400,00)
4 235,00
(3 500,00)
4 235,00
(3 500,00)
4 235,00
(3 500,00)
3 993,00
(3 300,00)
143,99
(119,00)
DISTRIBUČNÍ SAZBA
CENA ZA DODÁVKU ELEKTŘINY
CENA ZA DISTRIBUOVANÉ MNOŽSTVÍ ELEKTŘINY
99 999,00
(88 888,00)
Ceny uvedené tučně jsou včetně DPH ve výši 21 %, ceny uvedené v závorkách jsou bez DPH.
"""

FAVORIT3_TEXT = """
PRE PROUD FAVORIT 3 07/2026
Ceník elektřiny pro domácnosti platný od 1. 7. 2026
na distribučním území PREdistribuce, a. s.
Distribuční sazba
D01d, D02d D25d, D26d D27d D35d D45d D56d D57d D61d
Cena za spotřebovanou elektřinu
ve vysokém tarifu [Kč/MWh]
v nízkém tarifu [Kč/MWh]
Měsíční plat za odběrné místo
[Kč/měsíc]
3 690,50
(3 050,00)
3 569,50
(2 950,00)
3 569,50
(2 950,00)
3 690,50
(3 050,00)
3 932,50
(3 250,00)
3 932,50
(3 250,00)
3 932,50
(3 250,00)
3 569,50
(2 950,00)
— 3 327,50
(2 750,00)
3 327,50
(2 750,00)
3 448,50
(2 850,00)
3 569,50
(2 950,00)
3 569,50
(2 950,00)
3 569,50
(2 950,00)
3 327,50
(2 750,00)
156,09
(129,00)
CENA ZA DODÁVKU ELEKTŘINY
CENA ZA DISTRIBUOVANÉ MNOŽSTVÍ ELEKTŘINY
77 777,00
(66 666,00)
Ceny uvedené tučně jsou včetně DPH ve výši 21 %, ceny uvedené v závorkách jsou bez DPH.
"""


def load_parser():
    name = "pre_tariff_parser_test_module"
    sys.modules.pop(name, None)
    path = Path("custom_components/frakon_energy/providers/pre_tariff_parser.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_nefix_d25_parses_gross_supplier_prices_only() -> None:
    parser = load_parser()
    result = parser.parse_pre_supplier_tariff(
        NEFIX_TEXT,
        expected_product_name="PRE PROUD NEFIX",
        expected_distribution_tariff="D25d",
        expected_distributor="pre_distribuce",
        expected_valid_from=date(2026, 1, 1),
    )
    assert result.product_name == "PRE PROUD NEFIX"
    assert result.valid_from == date(2026, 1, 1)
    assert result.distribution_tariff == "D25d"
    assert result.high_rate_czk_per_kwh == Decimal("4.235")
    assert result.low_rate_czk_per_kwh == Decimal("3.993")
    assert result.supplier_standing_czk_month == Decimal("143.99")
    assert result.includes_vat is True


def test_single_rate_preserves_missing_low_rate() -> None:
    parser = load_parser()
    result = parser.parse_pre_supplier_tariff(
        NEFIX_TEXT,
        expected_product_name="PRE PROUD NEFIX",
        expected_distribution_tariff="D02d",
        expected_distributor="pre_distribuce",
        expected_valid_from=date(2026, 1, 1),
    )
    assert result.high_rate_czk_per_kwh == Decimal("4.356")
    assert result.low_rate_czk_per_kwh is None
    assert result.supplier_standing_czk_month == Decimal("143.99")


def test_favorit3_d57_uses_exact_july_candidate_prices() -> None:
    parser = load_parser()
    result = parser.parse_pre_supplier_tariff(
        FAVORIT3_TEXT,
        expected_product_name="PRE PROUD FAVORIT 3",
        expected_distribution_tariff="D57d",
        expected_distributor="pre_distribuce",
        expected_valid_from=date(2026, 7, 1),
    )
    assert result.high_rate_czk_per_kwh == Decimal("3.9325")
    assert result.low_rate_czk_per_kwh == Decimal("3.5695")
    assert result.supplier_standing_czk_month == Decimal("156.09")


def test_mutable_endpoint_newer_validity_is_rejected_instead_of_rebinding_candidate() -> None:
    parser = load_parser()
    august = FAVORIT3_TEXT.replace(
        "platný od 1. 7. 2026",
        "platný od 1. 8. 2026",
        1,
    ).replace("07/2026", "08/2026", 1)
    with pytest.raises(ValueError, match="immutable selected candidate"):
        parser.parse_pre_supplier_tariff(
            august,
            expected_product_name="PRE PROUD FAVORIT 3",
            expected_distribution_tariff="D25d",
            expected_distributor="pre_distribuce",
            expected_valid_from=date(2026, 7, 1),
        )


def test_wrong_distribution_territory_fails_closed() -> None:
    parser = load_parser()
    with pytest.raises(ValueError, match="distribution territory"):
        parser.parse_pre_supplier_tariff(
            NEFIX_TEXT,
            expected_product_name="PRE PROUD NEFIX",
            expected_distribution_tariff="D25d",
            expected_distributor="eg_d",
            expected_valid_from=date(2026, 1, 1),
        )


def test_wrong_product_fails_closed() -> None:
    parser = load_parser()
    with pytest.raises(ValueError, match="product marker"):
        parser.parse_pre_supplier_tariff(
            NEFIX_TEXT,
            expected_product_name="PRE PROUD FAVORIT 2",
            expected_distribution_tariff="D25d",
            expected_distributor="pre_distribuce",
            expected_valid_from=date(2026, 1, 1),
        )


def test_missing_vat_marker_fails_closed() -> None:
    parser = load_parser()
    invalid = NEFIX_TEXT.replace(
        "Ceny uvedené tučně jsou včetně DPH ve výši 21 %, ceny uvedené v závorkách jsou bez DPH.",
        "DPH není v dokumentu určena.",
    )
    with pytest.raises(ValueError, match="21% VAT convention"):
        parser.parse_pre_supplier_tariff(
            invalid,
            expected_product_name="PRE PROUD NEFIX",
            expected_distribution_tariff="D25d",
            expected_distributor="pre_distribuce",
            expected_valid_from=date(2026, 1, 1),
        )


def test_inconsistent_gross_net_pair_fails_closed() -> None:
    parser = load_parser()
    invalid = NEFIX_TEXT.replace("4 235,00\n(3 500,00)", "4 999,00\n(3 500,00)", 1)
    with pytest.raises(ValueError, match="gross/net pair"):
        parser.parse_pre_supplier_tariff(
            invalid,
            expected_product_name="PRE PROUD NEFIX",
            expected_distribution_tariff="D25d",
            expected_distributor="pre_distribuce",
            expected_valid_from=date(2026, 1, 1),
        )


def test_incomplete_matrix_fails_closed() -> None:
    parser = load_parser()
    invalid = NEFIX_TEXT.replace("143,99\n(119,00)", "143,99", 1)
    with pytest.raises(ValueError, match="incomplete"):
        parser.parse_pre_supplier_tariff(
            invalid,
            expected_product_name="PRE PROUD NEFIX",
            expected_distribution_tariff="D25d",
            expected_distributor="pre_distribuce",
            expected_valid_from=date(2026, 1, 1),
        )


def test_regulated_values_after_supplier_matrix_never_change_result() -> None:
    parser = load_parser()
    poisoned = NEFIX_TEXT.replace("99 999,00", "1 000 000,00").replace(
        "88 888,00", "826 446,28"
    )
    baseline = parser.parse_pre_supplier_tariff(
        NEFIX_TEXT,
        expected_product_name="PRE PROUD NEFIX",
        expected_distribution_tariff="D25d",
        expected_distributor="pre_distribuce",
        expected_valid_from=date(2026, 1, 1),
    )
    result = parser.parse_pre_supplier_tariff(
        poisoned,
        expected_product_name="PRE PROUD NEFIX",
        expected_distribution_tariff="D25d",
        expected_distributor="pre_distribuce",
        expected_valid_from=date(2026, 1, 1),
    )
    assert result == baseline
