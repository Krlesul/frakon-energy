from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
import importlib.util
from pathlib import Path
import sys
import types

import pytest


DAY = date(2026, 8, 15)


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_module(*, contract_mode="success", all_in_mode="success"):
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.all_in_catalog",
        "custom_components.frakon_energy.contracts",
        "custom_components.frakon_energy.cost",
        "custom_components.frakon_energy.billing_tariff_selection",
    )
    for name in names:
        sys.modules.pop(name, None)
    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    calls = []

    cost = types.ModuleType("custom_components.frakon_energy.cost")

    @dataclass(frozen=True, slots=True)
    class TariffPrices:
        high_rate_czk_per_kwh: Decimal
        low_rate_czk_per_kwh: Decimal
        fixed_monthly_czk: Decimal = Decimal("0")

    cost.TariffPrices = TariffPrices
    sys.modules[cost.__name__] = cost

    contracts = types.ModuleType("custom_components.frakon_energy.contracts")

    @dataclass(frozen=True)
    class Value:
        value: str

    @dataclass(frozen=True)
    class Breaker:
        code: str = "3x25A"

    @dataclass(frozen=True)
    class Contract:
        supplier: Value = Value("mnd")
        product_name: str = "Proud - Ceník Říjen 28"
        distribution_tariff: str = "D25d"
        breaker: Breaker = Breaker()

    def confirmed_contract_from_options(options, day):
        calls.append(("contract", dict(options), day))
        if contract_mode == "missing":
            raise LookupError("no confirmed contract")
        if contract_mode == "ambiguous":
            raise ValueError("ambiguous confirmed contracts")
        return Contract()

    contracts.confirmed_contract_from_options = confirmed_contract_from_options
    sys.modules[contracts.__name__] = contracts

    all_in = types.ModuleType("custom_components.frakon_energy.all_in_catalog")

    @dataclass(frozen=True)
    class Assembly:
        supplier: str = "mnd"
        product_name: str = "Proud - Ceník Říjen 28"
        distribution_tariff: str = "D25d"
        breaker_code: str = "3x25A"
        valid_from: date = date(2026, 6, 11)
        valid_to: date | None = date(2028, 10, 31)
        all_in_vt_czk_kwh: Decimal = Decimal("5.325243")
        all_in_nt_czk_kwh: Decimal = Decimal("4.460243")
        fixed_monthly_total_czk: Decimal = Decimal("388.2527")

    @dataclass(frozen=True)
    class Item:
        assembly: Assembly = Assembly()

    def confirmed_all_in_tariff_for_context_from_options(options, **kwargs):
        calls.append(("all_in", dict(options), dict(kwargs)))
        if all_in_mode == "missing":
            raise LookupError("no exact confirmed all-in")
        if all_in_mode == "ambiguous":
            raise ValueError("ambiguous confirmed all-in tariffs")
        return Item()

    def all_in_tariff_fingerprint(item):
        calls.append(("fingerprint", item))
        return "f" * 64

    all_in.confirmed_all_in_tariff_for_context_from_options = (
        confirmed_all_in_tariff_for_context_from_options
    )
    all_in.all_in_tariff_fingerprint = all_in_tariff_fingerprint
    sys.modules[all_in.__name__] = all_in

    module = _load(
        "custom_components.frakon_energy.billing_tariff_selection",
        "custom_components/frakon_energy/billing_tariff_selection.py",
    )
    return module, calls


def legacy_options(**overrides):
    values = {
        "price_vt_czk_kwh": "99.1",
        "price_nt_czk_kwh": "88.2",
        "fixed_monthly_czk": "777",
    }
    values.update(overrides)
    return values


def test_confirmed_all_in_overrides_legacy_prices_and_preserves_fixed_total() -> None:
    module, calls = load_module()
    result = module.billing_tariff_selection_for_day(legacy_options(), DAY)

    assert result.source is module.BillingTariffSource.CONFIRMED_ALL_IN
    assert result.uses_confirmed_all_in is True
    assert result.prices.high_rate_czk_per_kwh == Decimal("5.325243")
    assert result.prices.low_rate_czk_per_kwh == Decimal("4.460243")
    assert result.prices.fixed_monthly_czk == Decimal("388.2527")
    assert result.all_in_tariff_fingerprint == "f" * 64
    assert result.supplier == "mnd"
    assert result.product_name == "Proud - Ceník Říjen 28"
    assert result.distribution_tariff == "D25d"
    assert result.breaker_code == "3x25A"
    assert result.valid_from == date(2026, 6, 11)
    assert result.valid_to == date(2028, 10, 31)

    all_in_call = next(item for item in calls if item[0] == "all_in")
    assert all_in_call[2] == {
        "supplier": "mnd",
        "product_name": "Proud - Ceník Říjen 28",
        "distribution_tariff": "D25d",
        "breaker_code": "3x25A",
        "day": DAY,
    }


def test_missing_confirmed_customer_context_uses_legacy_migration_fallback() -> None:
    for contract_mode, all_in_mode in (
        ("missing", "success"),
        ("success", "missing"),
    ):
        module, _calls = load_module(
            contract_mode=contract_mode,
            all_in_mode=all_in_mode,
        )
        result = module.billing_tariff_selection_for_day(
            {
                "price_vt_czk_kwh": "7.52",
                "price_nt_czk_kwh": Decimal("4.67"),
                "fixed_monthly_czk": 300,
            },
            DAY,
        )
        assert result.source is module.BillingTariffSource.LEGACY_OPTIONS
        assert result.uses_confirmed_all_in is False
        assert result.prices.high_rate_czk_per_kwh == Decimal("7.52")
        assert result.prices.low_rate_czk_per_kwh == Decimal("4.67")
        assert result.prices.fixed_monthly_czk == Decimal("300")
        assert result.all_in_tariff_fingerprint is None


def test_ambiguous_confirmed_authority_never_falls_back_to_legacy_prices() -> None:
    module, _calls = load_module(contract_mode="ambiguous")
    with pytest.raises(ValueError, match="ambiguous confirmed contracts"):
        module.billing_tariff_selection_for_day(legacy_options(), DAY)

    module, _calls = load_module(all_in_mode="ambiguous")
    with pytest.raises(ValueError, match="ambiguous confirmed all-in tariffs"):
        module.billing_tariff_selection_for_day(legacy_options(), DAY)


def test_legacy_fallback_requires_complete_finite_nonnegative_prices() -> None:
    module, _calls = load_module(contract_mode="missing")
    with pytest.raises(LookupError, match="legacy billing prices are incomplete"):
        module.billing_tariff_selection_for_day({}, DAY)

    for field, value in (
        ("price_vt_czk_kwh", "NaN"),
        ("price_nt_czk_kwh", "Infinity"),
        ("fixed_monthly_czk", "-1"),
        ("price_vt_czk_kwh", True),
    ):
        with pytest.raises(ValueError, match="finite non-negative"):
            module.billing_tariff_selection_for_day(
                legacy_options(**{field: value}),
                DAY,
            )


def test_invalid_day_or_options_fail_before_selection() -> None:
    module, calls = load_module()
    with pytest.raises(ValueError, match="options must be a mapping"):
        module.billing_tariff_selection_for_day([], DAY)
    with pytest.raises(ValueError, match="day must be a date"):
        module.billing_tariff_selection_for_day({}, "2026-08-15")
    assert calls == []
