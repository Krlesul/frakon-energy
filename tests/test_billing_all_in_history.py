from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import importlib.util
from pathlib import Path
import sys

import pytest


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _fixture():
    helpers = _load(
        "_frakon_test_billing_history_customer_helpers",
        "tests/test_customer_tariff_proposals.py",
    )
    modules = helpers.load_modules()
    _load(
        "custom_components.frakon_energy.cost",
        "custom_components/frakon_energy/cost.py",
    )
    history = _load(
        "custom_components.frakon_energy.billing_all_in_history",
        "custom_components/frakon_energy/billing_all_in_history.py",
    )
    customer = modules[-1]
    all_in = modules[-2]
    authority = sys.modules["custom_components.frakon_energy.all_in_authority"]
    pricing = modules[0]

    options, proposal, version = helpers._staged(modules)
    options, _ = customer.confirm_customer_tariff_proposal(options, proposal.fingerprint)
    first = helpers._assembly(modules, version)

    second_variable = tuple(
        replace(
            component,
            high_rate_czk_per_kwh=component.high_rate_czk_per_kwh + Decimal("1.000"),
            low_rate_czk_per_kwh=component.low_rate_czk_per_kwh + Decimal("0.500"),
        )
        if component.kind == pricing.PriceComponentKind.COMMODITY
        else component
        for component in first.variable_components
    )
    second_fixed = tuple(
        replace(component, monthly_czk=component.monthly_czk + Decimal("10.00"))
        if component.kind == pricing.PriceComponentKind.SUPPLIER_FIXED
        else component
        for component in first.fixed_components
    )
    second = replace(
        first,
        valid_from=date(2026, 7, 1),
        variable_components=second_variable,
        fixed_components=second_fixed,
    )
    options = all_in.append_all_in_tariff(options, second)
    second_item = next(
        item
        for item in all_in.all_in_tariffs_from_options(options)
        if item.assembly.valid_from == date(2026, 7, 1)
    )
    second_fingerprint = all_in.all_in_tariff_fingerprint(second_item)
    options = all_in.confirm_all_in_tariff(options, second_fingerprint)
    options = authority.append_all_in_tariff_authority(
        options,
        all_in_fingerprint=second_fingerprint,
        method=authority.AllInTariffAuthorityMethod.VERIFIED_PARSER,
    )
    return history, authority, first, second, proposal, second_fingerprint, options


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def test_schedule_compresses_exact_confirmed_historical_versions() -> None:
    history, authority, first, second, proposal, second_fingerprint, options = _fixture()

    schedule = history.confirmed_all_in_billing_schedule(
        options,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
    )

    assert len(schedule) == 2
    assert schedule[0].valid_from == date(2026, 1, 1)
    assert schedule[0].valid_to == date(2026, 6, 30)
    assert schedule[0].day_count == 181
    assert schedule[0].all_in_tariff_fingerprint == proposal.all_in_tariff_fingerprint
    assert schedule[0].prices.high_rate_czk_per_kwh == first.all_in_vt_czk_kwh
    assert schedule[1].valid_from == date(2026, 7, 1)
    assert schedule[1].valid_to == date(2026, 12, 31)
    assert schedule[1].day_count == 184
    assert schedule[1].all_in_tariff_fingerprint == second_fingerprint
    assert schedule[1].prices.high_rate_czk_per_kwh == second.all_in_vt_czk_kwh
    assert all(
        segment.authority_method is authority.AllInTariffAuthorityMethod.VERIFIED_PARSER
        for segment in schedule
    )


def test_projection_prices_elapsed_and_future_days_with_their_own_tariff_version() -> None:
    history, _authority, first, second, _proposal, _second_fingerprint, options = _fixture()

    result = history.calculate_confirmed_all_in_cost_projection(
        options,
        cycle_start=date(2026, 1, 1),
        settlement_date=date(2026, 12, 31),
        as_of=date(2026, 9, 30),
        baseline_high_rate_kwh=Decimal("0"),
        baseline_low_rate_kwh=Decimal("0"),
        current_high_rate_kwh=Decimal("273"),
        current_low_rate_kwh=Decimal("546"),
    )

    old_daily_energy = first.all_in_vt_czk_kwh + Decimal("2") * first.all_in_nt_czk_kwh
    new_daily_energy = second.all_in_vt_czk_kwh + Decimal("2") * second.all_in_nt_czk_kwh
    accrued_energy = Decimal("181") * old_daily_energy + Decimal("92") * new_daily_energy
    projected_energy = Decimal("181") * old_daily_energy + Decimal("184") * new_daily_energy
    accrued_fixed = (
        Decimal("181") * first.fixed_monthly_total_czk
        + Decimal("92") * second.fixed_monthly_total_czk
    ) * Decimal("12") / Decimal("365")
    projected_fixed = (
        Decimal("181") * first.fixed_monthly_total_czk
        + Decimal("184") * second.fixed_monthly_total_czk
    ) * Decimal("12") / Decimal("365")

    assert result.method == "daily_confirmed_all_in_schedule_linear_consumption"
    assert result.tariff_version_count == 2
    assert result.cost.high_rate_consumption_kwh == Decimal("273.000")
    assert result.cost.low_rate_consumption_kwh == Decimal("546.000")
    assert result.cost.accrued_energy_cost_czk == _money(accrued_energy)
    assert result.cost.accrued_fixed_cost_czk == _money(accrued_fixed)
    assert result.cost.accrued_total_cost_czk == _money(accrued_energy + accrued_fixed)
    assert result.cost.projected_total_cost_czk == _money(projected_energy + projected_fixed)


def test_projection_is_not_equivalent_to_repricing_entire_cycle_with_latest_tariff() -> None:
    history, _authority, _first, second, _proposal, _second_fingerprint, options = _fixture()

    result = history.calculate_confirmed_all_in_cost_projection(
        options,
        cycle_start=date(2026, 1, 1),
        settlement_date=date(2026, 12, 31),
        as_of=date(2026, 9, 30),
        baseline_high_rate_kwh=Decimal("0"),
        baseline_low_rate_kwh=Decimal("0"),
        current_high_rate_kwh=Decimal("273"),
        current_low_rate_kwh=Decimal("546"),
    )

    latest_daily = second.all_in_vt_czk_kwh + Decimal("2") * second.all_in_nt_czk_kwh
    latest_only_energy = latest_daily * Decimal("365")
    latest_only_fixed = second.fixed_monthly_total_czk * Decimal("12")
    assert result.cost.projected_total_cost_czk != _money(
        latest_only_energy + latest_only_fixed
    )


def test_missing_authority_for_any_historical_version_fails_closed() -> None:
    history, authority, _first, _second, _proposal, second_fingerprint, options = _fixture()
    broken = dict(options)
    broken[authority.OPTION_ALL_IN_TARIFF_AUTHORITIES] = [
        item
        for item in broken[authority.OPTION_ALL_IN_TARIFF_AUTHORITIES]
        if item["all_in_tariff_fingerprint"] != second_fingerprint
    ]

    with pytest.raises(LookupError, match="all-in tariff authority not found"):
        history.confirmed_all_in_billing_schedule(
            broken,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )


def test_missing_confirmed_tariff_for_any_cycle_day_fails_closed() -> None:
    history, authority, _first, _second, _proposal, _second_fingerprint, options = _fixture()
    all_in = sys.modules["custom_components.frakon_energy.all_in_catalog"]
    first_item = next(
        item
        for item in all_in.all_in_tariffs_from_options(options)
        if item.assembly.valid_from == date(2026, 1, 1)
    )
    limited_item = all_in.PersistedAllInTariff(
        assembly=replace(first_item.assembly, valid_to=date(2026, 6, 30)),
        confirmed=True,
    )
    limited_fingerprint = all_in.all_in_tariff_fingerprint(limited_item)
    broken = dict(options)
    broken[all_in.OPTION_ALL_IN_TARIFF_CATALOG] = [limited_item.as_dict()]
    broken[authority.OPTION_ALL_IN_TARIFF_AUTHORITIES] = []
    broken = authority.append_all_in_tariff_authority(
        broken,
        all_in_fingerprint=limited_fingerprint,
        method=authority.AllInTariffAuthorityMethod.VERIFIED_PARSER,
    )

    with pytest.raises(LookupError, match="No confirmed all-in tariff applies"):
        history.confirmed_all_in_billing_schedule(
            broken,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
