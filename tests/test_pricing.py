from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys


def load_pricing():
    path = Path("custom_components/frakon_energy/pricing.py")
    spec = importlib.util.spec_from_file_location("frakon_energy_pricing", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_all_in_price_sums_variable_and_fixed_components() -> None:
    pricing = load_pricing()
    tariff = pricing.AllInTariffPrice(
        source=pricing.PriceSource(
            supplier="ČEZ",
            product="Elektřina na 3 roky",
            valid_from=date(2026, 1, 1),
            confirmed=True,
        ),
        variable_components=(
            pricing.VariablePriceComponent(pricing.PriceComponentKind.COMMODITY, "Silová elektřina", Decimal("3.20"), Decimal("2.80")),
            pricing.VariablePriceComponent(pricing.PriceComponentKind.DISTRIBUTION, "Distribuce", Decimal("2.10"), Decimal("0.75")),
            pricing.VariablePriceComponent(pricing.PriceComponentKind.POZE, "POZE", Decimal("0.60"), Decimal("0.60")),
            pricing.VariablePriceComponent(pricing.PriceComponentKind.SYSTEM_SERVICES, "Systémové služby", Decimal("0.25"), Decimal("0.25")),
            pricing.VariablePriceComponent(pricing.PriceComponentKind.ELECTRICITY_TAX, "Daň", Decimal("0.034"), Decimal("0.034")),
        ),
        fixed_components=(
            pricing.FixedPriceComponent(pricing.PriceComponentKind.SUPPLIER_FIXED, "Stálý plat dodavateli", Decimal("129")),
            pricing.FixedPriceComponent(pricing.PriceComponentKind.BREAKER_FIXED, "Jistič 3x25 A", Decimal("286")),
        ),
    )

    assert tariff.all_in_vt_czk_kwh == Decimal("6.184")
    assert tariff.all_in_nt_czk_kwh == Decimal("4.434")
    assert tariff.fixed_monthly_total_czk == Decimal("415")
    assert tariff.high_rate_czk_per_kwh == tariff.all_in_vt_czk_kwh
    assert tariff.low_rate_czk_per_kwh == tariff.all_in_nt_czk_kwh
    assert tariff.fixed_monthly_czk == tariff.fixed_monthly_total_czk


def test_net_components_are_normalized_to_gross_all_in_totals() -> None:
    pricing = load_pricing()
    tariff = pricing.AllInTariffPrice(
        source=pricing.PriceSource(
            supplier="E.ON",
            product="Test",
            valid_from=date(2026, 1, 1),
            confirmed=True,
        ),
        variable_components=(
            pricing.VariablePriceComponent(
                pricing.PriceComponentKind.COMMODITY,
                "Komodita netto",
                Decimal("1.00"),
                Decimal("0.50"),
                includes_vat=False,
                vat_rate_percent=Decimal("21"),
            ),
            pricing.VariablePriceComponent(
                pricing.PriceComponentKind.DISTRIBUTION,
                "Distribuce brutto",
                Decimal("0.79"),
                Decimal("0.395"),
                includes_vat=True,
            ),
        ),
        fixed_components=(
            pricing.FixedPriceComponent(
                pricing.PriceComponentKind.SUPPLIER_FIXED,
                "Stálý plat netto",
                Decimal("100"),
                includes_vat=False,
                vat_rate_percent=Decimal("21"),
            ),
        ),
    )

    assert tariff.all_in_vt_czk_kwh == Decimal("2.0000")
    assert tariff.all_in_nt_czk_kwh == Decimal("1.000")
    assert tariff.fixed_monthly_total_czk == Decimal("121")
    assert tariff.variable_breakdown()["Komodita netto"] == {
        "vt_czk_per_kwh": Decimal("1.2100"),
        "nt_czk_per_kwh": Decimal("0.6050"),
    }
    assert tariff.fixed_breakdown()["Stálý plat netto"] == Decimal("121")


def test_tariff_price_round_trip_preserves_decimal_dates_and_source_metadata() -> None:
    pricing = load_pricing()
    tariff = pricing.AllInTariffPrice(
        source=pricing.PriceSource(
            supplier="ČEZ",
            product="Fix 2026",
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
            source_url="https://example.invalid/cenik.pdf",
            document_date=date(2025, 12, 1),
            checksum="sha256:abc123",
            confirmed=True,
        ),
        variable_components=(
            pricing.VariablePriceComponent(
                pricing.PriceComponentKind.COMMODITY,
                "Komodita",
                Decimal("3.1000"),
                Decimal("2.7000"),
                includes_vat=False,
                vat_rate_percent=Decimal("21"),
            ),
        ),
        fixed_components=(
            pricing.FixedPriceComponent(
                pricing.PriceComponentKind.BREAKER_FIXED,
                "Jistič",
                Decimal("286.50"),
            ),
        ),
    )

    payload = tariff.as_dict()
    assert payload["schema_version"] == pricing.TARIFF_PRICE_SCHEMA_VERSION
    assert payload["variable_components"][0]["high_rate_czk_per_kwh"] == "3.1000"
    assert payload["source"]["valid_from"] == "2026-01-01"

    restored = pricing.AllInTariffPrice.from_dict(payload)
    assert restored == tariff
    assert restored.all_in_vt_czk_kwh == Decimal("3.751000")
    assert restored.source.checksum == "sha256:abc123"


def test_tariff_deserialization_rejects_unknown_schema_and_invalid_period() -> None:
    pricing = load_pricing()
    payload = pricing.AllInTariffPrice(
        source=pricing.PriceSource("ČEZ", "Produkt", date(2026, 1, 1)),
        variable_components=(),
        fixed_components=(),
    ).as_dict()
    payload["schema_version"] = 999

    try:
        pricing.AllInTariffPrice.from_dict(payload)
    except ValueError as err:
        assert "schema version" in str(err)
    else:
        raise AssertionError("Unknown tariff schema must be rejected")

    try:
        pricing.PriceSource(
            "ČEZ",
            "Produkt",
            date(2026, 2, 1),
            valid_to=date(2026, 1, 31),
        )
    except ValueError as err:
        assert "must not precede" in str(err)
    else:
        raise AssertionError("Invalid source validity interval must be rejected")


def test_latest_overlapping_validity_period_wins() -> None:
    pricing = load_pricing()
    old = pricing.AllInTariffPrice(
        source=pricing.PriceSource("ČEZ", "Produkt", date(2026, 1, 1), date(2026, 6, 30)),
        variable_components=(),
        fixed_components=(),
    )
    new = pricing.AllInTariffPrice(
        source=pricing.PriceSource("ČEZ", "Produkt", date(2026, 6, 1)),
        variable_components=(),
        fixed_components=(),
    )
    assert pricing.select_price_for_day((old, new), date(2026, 6, 15)) is new


def test_confirmed_selector_never_activates_newer_unconfirmed_tariff() -> None:
    pricing = load_pricing()
    confirmed = pricing.AllInTariffPrice(
        source=pricing.PriceSource(
            "ČEZ",
            "Potvrzený produkt",
            date(2026, 1, 1),
            confirmed=True,
        ),
        variable_components=(),
        fixed_components=(),
    )
    suggested = pricing.AllInTariffPrice(
        source=pricing.PriceSource(
            "ČEZ",
            "Novější návrh",
            date(2026, 6, 1),
            confirmed=False,
        ),
        variable_components=(),
        fixed_components=(),
    )

    assert pricing.select_price_for_day((confirmed, suggested), date(2026, 8, 1)) is suggested
    assert pricing.select_confirmed_price_for_day(
        (confirmed, suggested), date(2026, 8, 1)
    ) is confirmed


def test_invalid_price_components_are_rejected() -> None:
    pricing = load_pricing()
    for invalid in (Decimal("-1"), Decimal("Infinity"), Decimal("NaN")):
        try:
            pricing.VariablePriceComponent(
                pricing.PriceComponentKind.DISTRIBUTION,
                "Distribuce",
                invalid,
                Decimal("0"),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid variable price must be rejected")

    try:
        pricing.FixedPriceComponent(
            pricing.PriceComponentKind.BREAKER_FIXED,
            "Jistič",
            Decimal("100"),
            includes_vat=False,
            vat_rate_percent=Decimal("-1"),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Negative VAT rate must be rejected")
