from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types


def load_modules():
    for name in (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.pricing",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.regulated_pricing",
    ):
        sys.modules.pop(name, None)

    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    def load(name: str, path: str):
        spec = importlib.util.spec_from_file_location(name, Path(path))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    pricing = load(
        "custom_components.frakon_energy.pricing",
        "custom_components/frakon_energy/pricing.py",
    )
    sources = load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    regulated = load(
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components/frakon_energy/regulated_pricing.py",
    )
    return pricing, sources, regulated


def _bundle(pricing, regulated, *, confirmed: bool = False):
    return regulated.RegulatedTariffBundle(
        distributor="cez_distribuce",
        distribution_tariff="d25D",
        breaker_code="3x25A",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        variable_components=(
            pricing.VariablePriceComponent(
                kind=pricing.PriceComponentKind.DISTRIBUTION,
                name="Distribuce",
                high_rate_czk_per_kwh=Decimal("1.20"),
                low_rate_czk_per_kwh=Decimal("0.40"),
            ),
            pricing.VariablePriceComponent(
                kind=pricing.PriceComponentKind.POZE,
                name="POZE",
                high_rate_czk_per_kwh=Decimal("0.00"),
                low_rate_czk_per_kwh=Decimal("0.00"),
            ),
        ),
        fixed_components=(
            pricing.FixedPriceComponent(
                kind=pricing.PriceComponentKind.BREAKER_FIXED,
                name="Plat za jistič",
                monthly_czk=Decimal("250.00"),
            ),
        ),
        source_url="https://www.example-regulator.cz/cenik.pdf",
        document_date=date(2025, 11, 30),
        checksum="a" * 64,
        confirmed=confirmed,
    )


def test_regulated_bundle_keeps_scope_and_exact_customer_match() -> None:
    pricing, sources, regulated = load_modules()
    bundle = _bundle(pricing, regulated, confirmed=True)

    assert bundle.distribution_tariff == "D25d"
    assert bundle.price_scope == sources.PRICE_SCOPE_REGULATED
    assert bundle.all_in_ready is False
    assert bundle.matches_customer_tariff(
        distribution_tariff="D25d",
        breaker_code="3x25A",
        day=date(2026, 8, 14),
    ) is True
    assert bundle.matches_customer_tariff(
        distribution_tariff="D25d",
        breaker_code="3x20A",
        day=date(2026, 8, 14),
    ) is False
    assert bundle.matches_customer_tariff(
        distribution_tariff="D27d",
        breaker_code="3x25A",
        day=date(2026, 8, 14),
    ) is False


def test_unconfirmed_regulated_bundle_cannot_match_by_default() -> None:
    pricing, _, regulated = load_modules()
    bundle = _bundle(pricing, regulated, confirmed=False)

    assert bundle.matches_customer_tariff(
        distribution_tariff="D25d",
        breaker_code="3x25A",
        day=date(2026, 8, 14),
    ) is False
    assert bundle.matches_customer_tariff(
        distribution_tariff="D25d",
        breaker_code="3x25A",
        day=date(2026, 8, 14),
        require_confirmation=False,
    ) is True


def test_regulated_bundle_rejects_supplier_component_kinds() -> None:
    pricing, _, regulated = load_modules()

    try:
        regulated.RegulatedTariffBundle(
            distributor="cez_distribuce",
            distribution_tariff="D25d",
            breaker_code="3x25A",
            valid_from=date(2026, 1, 1),
            variable_components=(
                pricing.VariablePriceComponent(
                    kind=pricing.PriceComponentKind.COMMODITY,
                    name="Wrong supplier commodity",
                    high_rate_czk_per_kwh=Decimal("3.9"),
                    low_rate_czk_per_kwh=Decimal("3.7"),
                ),
            ),
            fixed_components=(
                pricing.FixedPriceComponent(
                    kind=pricing.PriceComponentKind.BREAKER_FIXED,
                    name="Plat za jistič",
                    monthly_czk=Decimal("250"),
                ),
            ),
            source_url="https://www.example-regulator.cz/cenik.pdf",
        )
    except ValueError as err:
        assert "supplier or unsupported kind" in str(err)
    else:
        raise AssertionError("Supplier commodity must not enter regulated bundle")

    try:
        regulated.RegulatedTariffBundle(
            distributor="cez_distribuce",
            distribution_tariff="D25d",
            breaker_code="3x25A",
            valid_from=date(2026, 1, 1),
            variable_components=(
                pricing.VariablePriceComponent(
                    kind=pricing.PriceComponentKind.DISTRIBUTION,
                    name="Distribuce",
                    high_rate_czk_per_kwh=Decimal("1.2"),
                    low_rate_czk_per_kwh=Decimal("0.4"),
                ),
            ),
            fixed_components=(
                pricing.FixedPriceComponent(
                    kind=pricing.PriceComponentKind.SUPPLIER_FIXED,
                    name="Wrong supplier fixed",
                    monthly_czk=Decimal("120"),
                ),
            ),
            source_url="https://www.example-regulator.cz/cenik.pdf",
        )
    except ValueError as err:
        assert "supplier or unsupported kind" in str(err)
    else:
        raise AssertionError("Supplier standing charge must not enter regulated bundle")


def test_regulated_bundle_rejects_unsafe_source_and_duplicate_names() -> None:
    pricing, _, regulated = load_modules()

    for url in (
        "http://www.example-regulator.cz/cenik.pdf",
        "https://user:pass@www.example-regulator.cz/cenik.pdf",
        "https://www.example-regulator.cz:8443/cenik.pdf",
    ):
        try:
            regulated.RegulatedTariffBundle(
                distributor="cez_distribuce",
                distribution_tariff="D25d",
                breaker_code="3x25A",
                valid_from=date(2026, 1, 1),
                variable_components=(
                    pricing.VariablePriceComponent(
                        kind=pricing.PriceComponentKind.DISTRIBUTION,
                        name="Distribuce",
                        high_rate_czk_per_kwh=Decimal("1.2"),
                        low_rate_czk_per_kwh=Decimal("0.4"),
                    ),
                ),
                fixed_components=(
                    pricing.FixedPriceComponent(
                        kind=pricing.PriceComponentKind.BREAKER_FIXED,
                        name="Jistič",
                        monthly_czk=Decimal("250"),
                    ),
                ),
                source_url=url,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Unsafe regulated source URL must be rejected")

    try:
        regulated.RegulatedTariffBundle(
            distributor="cez_distribuce",
            distribution_tariff="D25d",
            breaker_code="3x25A",
            valid_from=date(2026, 1, 1),
            variable_components=(
                pricing.VariablePriceComponent(
                    kind=pricing.PriceComponentKind.DISTRIBUTION,
                    name="Duplicitní název",
                    high_rate_czk_per_kwh=Decimal("1.2"),
                    low_rate_czk_per_kwh=Decimal("0.4"),
                ),
            ),
            fixed_components=(
                pricing.FixedPriceComponent(
                    kind=pricing.PriceComponentKind.BREAKER_FIXED,
                    name="Duplicitní název",
                    monthly_czk=Decimal("250"),
                ),
            ),
            source_url="https://www.example-regulator.cz/cenik.pdf",
        )
    except ValueError as err:
        assert "names must be unique" in str(err)
    else:
        raise AssertionError("Duplicate regulated component names must be rejected")
