from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys

import pytest


def _load():
    name = "custom_components.frakon_energy.providers.mnd_tariff_parser"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        name,
        Path("custom_components/frakon_energy/providers/mnd_tariff_parser.py"),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Frozen minimal text excerpt from the official three-page MND household PDF
# `Proud – Ceník Léto 28 - Domácnosti`.  Only the first supplier-commercial
# comparison table is represented, followed by the regulated boundary and a
# deliberately unrelated number that the parser must never consume.
MND_LETO_28_TEXT = """
CENÍK
Produkt Proud – Ceník Léto 28 - Domácnosti
Ceny obchodní za elektřinu
pro domácnosti s DPH [bez DPH]
platné od 1. 1. 2026 v distribuční oblasti ČEZ Distribuce, a.s.
Proud – Ceník Léto 28
Výhodná obchodní cena elektřiny do konce června 2028.
Do 30. 6. 2028 máte jistotu ceny za elektřinu.

Distribuční
sazba
Obchodní sazby dle spotřeby
Cena za elektřinu v Kč za MWh ve vysokém tarifu
Cena za elektřinu v Kč za MWh v nízkém tarifu
Měsíční platba v Kč
Proud
Ceník Léto 28
Proud
Základní ceník
Proud
Ceník Léto 28
Proud
Základní ceník
Proud
Ceník Léto 28
Proud
Základní ceník

D01dD02d
Standard
2 899 [2 395,86]
4 074 [3 366,94]
— —
157 [129,75]
120 [99,17]
D25dD26d
Aku 8H
3 073 [2 539,66]
4 170 [3 446,28]
2 797 [2 311,57]
3 795 [3 136,36]
D27d
Elektromobil
3 073 [2 539,66]
4 170 [3 446,28]
2 797 [2 311,57]
3 795 [3 136,36]
D35d
Aku 16H
3 120 [2 578,51]
4 193 [3 465,29]
2 967 [2 452,07]
3 989 [3 296,69]
D45d
Přímotop
3 110 [2 570,25]
4 182 [3 456,20]
2 983 [2 465,29]
4 002 [3 307,44]
D56d
Tepelné
čerpadlo
3 110 [2 570,25]
4 182 [3 456,20]
2 983 [2 465,29]
4 002 [3 307,44]
D57d
Elektrické
topení
3 110 [2 570,25]
4 182 [3 456,20]
2 983 [2 465,29]
4 002 [3 307,44]
D61d
Víkend
2 899 [2 395,86]
4 074 [3 366,94]
2 899 [2 395,86]
4 074 [3 366,94]

Ceny s DPH [bez DPH]
Příloha ceníku
Ceny a sazby regulované
D25d regulovaný test 9 999 [8 263,64]
"""


def _parse(parser, **overrides):
    kwargs = {
        "expected_product_name": "Proud - Ceník Léto 28",
        "expected_distribution_tariff": "D25d",
        "expected_distributor": "cez_distribuce",
        "expected_valid_from": date(2026, 1, 1),
        "expected_valid_to": date(2028, 6, 30),
    }
    kwargs.update(overrides)
    return parser.parse_mnd_supplier_tariff(MND_LETO_28_TEXT, **kwargs)


def test_parse_d25d_current_product_column_and_ignore_regulated_appendix() -> None:
    parser = _load()
    result = _parse(parser)

    assert result.product_name == "Proud - Ceník Léto 28"
    assert result.distribution_tariff == "D25d"
    assert result.distributor == "cez_distribuce"
    assert result.high_rate_czk_per_kwh == Decimal("3.073")
    assert result.low_rate_czk_per_kwh == Decimal("2.797")
    assert result.supplier_standing_czk_month == Decimal("157")
    assert result.valid_from == date(2026, 1, 1)
    assert result.valid_to == date(2028, 6, 30)
    assert result.includes_vat is True
    assert result.high_rate_czk_per_kwh != Decimal("9.999")


def test_parse_other_dual_rate_and_single_rate_without_inventing_nt() -> None:
    parser = _load()
    d57 = _parse(parser, expected_distribution_tariff="D57d")
    assert d57.high_rate_czk_per_kwh == Decimal("3.110")
    assert d57.low_rate_czk_per_kwh == Decimal("2.983")
    assert d57.supplier_standing_czk_month == Decimal("157")

    d02 = _parse(parser, expected_distribution_tariff="D02d")
    assert d02.high_rate_czk_per_kwh == Decimal("2.899")
    assert d02.low_rate_czk_per_kwh is None


def test_current_fixed_and_basic_product_families_are_supported_but_decreasing_is_not() -> None:
    parser = _load()
    assert parser.mnd_automatic_parser_supports_product("Proud - Ceník Říjen 28") is True
    assert parser.mnd_automatic_parser_supports_product("Proud - Domácnosti") is True
    assert (
        parser.mnd_automatic_parser_supports_product(
            "Proud - Klesající ceník Duben 29"
        )
        is False
    )
    with pytest.raises(LookupError, match="does not support product"):
        _parse(
            parser,
            expected_product_name="Proud - Klesající ceník Duben 29",
        )


def test_product_validity_and_distributor_drift_fail_closed() -> None:
    parser = _load()
    with pytest.raises(ValueError, match="product identity mismatch"):
        _parse(parser, expected_product_name="Proud - Ceník Říjen 28")
    with pytest.raises(ValueError, match="validity start"):
        _parse(parser, expected_valid_from=date(2026, 2, 1))
    with pytest.raises(ValueError, match="fixed-price end date"):
        _parse(parser, expected_valid_to=date(2028, 10, 31))
    with pytest.raises(ValueError, match="distribution area"):
        _parse(parser, expected_distributor="eg_d")


def test_missing_regulated_boundary_or_commercial_table_fails_closed() -> None:
    parser = _load()
    with pytest.raises(ValueError, match="regulated-section marker"):
        parser.parse_mnd_supplier_tariff(
            MND_LETO_28_TEXT.replace("Ceny a sazby regulované", "Regulace"),
            expected_product_name="Proud - Ceník Léto 28",
            expected_distribution_tariff="D25d",
            expected_distributor="cez_distribuce",
            expected_valid_from=date(2026, 1, 1),
            expected_valid_to=date(2028, 6, 30),
        )
    with pytest.raises(ValueError, match="commercial table"):
        parser.parse_mnd_supplier_tariff(
            MND_LETO_28_TEXT.replace("Distribuční\nsazba", "Tarif"),
            expected_product_name="Proud - Ceník Léto 28",
            expected_distribution_tariff="D25d",
            expected_distributor="cez_distribuce",
            expected_valid_from=date(2026, 1, 1),
            expected_valid_to=date(2028, 6, 30),
        )


def test_gross_net_vat_drift_or_layout_drift_is_rejected() -> None:
    parser = _load()
    bad_vat = MND_LETO_28_TEXT.replace(
        "3 073 [2 539,66]",
        "3 073 [2 400,00]",
        1,
    )
    with pytest.raises(ValueError, match="VAT pair is inconsistent"):
        parser.parse_mnd_supplier_tariff(
            bad_vat,
            expected_product_name="Proud - Ceník Léto 28",
            expected_distribution_tariff="D25d",
            expected_distributor="cez_distribuce",
            expected_valid_from=date(2026, 1, 1),
            expected_valid_to=date(2028, 6, 30),
        )

    bad_layout = MND_LETO_28_TEXT.replace("D61d\nVíkend", "Víkend", 1)
    with pytest.raises(ValueError, match="tariff group is missing"):
        parser.parse_mnd_supplier_tariff(
            bad_layout,
            expected_product_name="Proud - Ceník Léto 28",
            expected_distribution_tariff="D25d",
            expected_distributor="cez_distribuce",
            expected_valid_from=date(2026, 1, 1),
            expected_valid_to=date(2028, 6, 30),
        )
