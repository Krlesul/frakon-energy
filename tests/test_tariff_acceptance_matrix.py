"""Acceptance vectors for regulated tariff identity and validity boundaries.

These tests intentionally avoid hard-coding live market prices. They lock the M6
catalog contract that official price fixtures can later reuse: canonical Czech
rate codes, exact breaker matching, inclusive validity boundaries, and deterministic
handoff between consecutive tariff versions.
"""

from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types

import pytest


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
    load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    regulated = load(
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components/frakon_energy/regulated_pricing.py",
    )
    return pricing, regulated


def _bundle(
    pricing,
    regulated,
    *,
    tariff: str,
    breaker: str = "3x25A",
    valid_from: date = date(2026, 1, 1),
    valid_to: date | None = date(2026, 12, 31),
):
    return regulated.RegulatedTariffBundle(
        distributor="cez_distribuce",
        distribution_tariff=tariff,
        breaker_code=breaker,
        valid_from=valid_from,
        valid_to=valid_to,
        variable_components=(
            pricing.VariablePriceComponent(
                kind=pricing.PriceComponentKind.DISTRIBUTION,
                name="Distribuce",
                high_rate_czk_per_kwh=Decimal("1.20"),
                low_rate_czk_per_kwh=Decimal("0.40"),
            ),
        ),
        fixed_components=(
            pricing.FixedPriceComponent(
                kind=pricing.PriceComponentKind.BREAKER_FIXED,
                name="Plat za jistič",
                monthly_czk=Decimal("250.00"),
            ),
        ),
        source_url="https://eru.gov.cz/acceptance-vector.pdf",
        checksum="a" * 64,
        confirmed=True,
    )


@pytest.mark.parametrize(
    ("raw_tariff", "canonical"),
    (
        ("d02D", "D02d"),
        ("d25D", "D25d"),
        ("D57D", "D57d"),
    ),
)
def test_m6_rate_vectors_normalize_and_match_exact_customer_tariff(
    raw_tariff: str,
    canonical: str,
) -> None:
    pricing, regulated = load_modules()
    bundle = _bundle(pricing, regulated, tariff=raw_tariff)

    assert bundle.distribution_tariff == canonical
    assert bundle.matches_customer_tariff(
        distribution_tariff=canonical,
        breaker_code="3x25A",
        day=date(2026, 8, 16),
    ) is True
    assert bundle.matches_customer_tariff(
        distribution_tariff=canonical,
        breaker_code="3x20A",
        day=date(2026, 8, 16),
    ) is False


def test_m6_validity_window_is_inclusive_and_closed_outside_boundaries() -> None:
    pricing, regulated = load_modules()
    bundle = _bundle(
        pricing,
        regulated,
        tariff="D25d",
        valid_from=date(2026, 3, 1),
        valid_to=date(2026, 10, 31),
    )

    assert bundle.applies_on(date(2026, 2, 28)) is False
    assert bundle.applies_on(date(2026, 3, 1)) is True
    assert bundle.applies_on(date(2026, 10, 31)) is True
    assert bundle.applies_on(date(2026, 11, 1)) is False


def test_m6_consecutive_price_year_versions_handoff_on_exact_day() -> None:
    pricing, regulated = load_modules()
    old = _bundle(
        pricing,
        regulated,
        tariff="D57d",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
    )
    new = _bundle(
        pricing,
        regulated,
        tariff="D57d",
        valid_from=date(2027, 1, 1),
        valid_to=date(2027, 12, 31),
    )

    assert old.matches_customer_tariff(
        distribution_tariff="D57d",
        breaker_code="3x25A",
        day=date(2026, 12, 31),
    ) is True
    assert new.matches_customer_tariff(
        distribution_tariff="D57d",
        breaker_code="3x25A",
        day=date(2026, 12, 31),
    ) is False

    assert old.matches_customer_tariff(
        distribution_tariff="D57d",
        breaker_code="3x25A",
        day=date(2027, 1, 1),
    ) is False
    assert new.matches_customer_tariff(
        distribution_tariff="D57d",
        breaker_code="3x25A",
        day=date(2027, 1, 1),
    ) is True


@pytest.mark.parametrize("invalid_tariff", ("D2d", "C25d", "D250d", "D25"))
def test_m6_invalid_rate_codes_fail_closed(invalid_tariff: str) -> None:
    pricing, regulated = load_modules()

    with pytest.raises(ValueError, match="distribution_tariff"):
        _bundle(pricing, regulated, tariff=invalid_tariff)
