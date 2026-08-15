from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
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


def _modules():
    helpers = _load(
        "_frakon_test_billing_tariff_customer_helpers",
        "tests/test_customer_tariff_proposals.py",
    )
    modules = helpers.load_modules()
    cost = _load(
        "custom_components.frakon_energy.cost",
        "custom_components/frakon_energy/cost.py",
    )
    selector = _load(
        "custom_components.frakon_energy.billing_tariff_selection",
        "custom_components/frakon_energy/billing_tariff_selection.py",
    )
    return helpers, modules, cost, selector


def _legacy(cost):
    return cost.TariffPrices(
        high_rate_czk_per_kwh=Decimal("9.99"),
        low_rate_czk_per_kwh=Decimal("8.88"),
        fixed_monthly_czk=Decimal("777"),
    )


def _confirmed(*, manual=False):
    helpers, modules, cost, selector = _modules()
    regulated_catalog = modules[-3]
    customer = modules[-1]
    authority = sys.modules["custom_components.frakon_energy.all_in_authority"]
    version = helpers._regulated_version(modules)
    assembly = helpers._assembly(modules, version)
    options = regulated_catalog.append_confirmed_regulated_tariff({}, version)
    options, proposal = customer.stage_customer_tariff_proposal(
        options,
        contract=helpers._contract(modules),
        assembly=assembly,
        candidate_fingerprint="c" * 64,
        regulated_version_fingerprint=version.fingerprint,
        proposed_for_day=date(2026, 8, 14),
        proposed_at=datetime(2026, 8, 15, 19, 15, tzinfo=timezone.utc),
        authority_method=(
            authority.AllInTariffAuthorityMethod.MANUAL_USER_ENTRY
            if manual
            else authority.AllInTariffAuthorityMethod.VERIFIED_PARSER
        ),
    )
    options, _ = customer.confirm_customer_tariff_proposal(
        options,
        proposal.fingerprint,
    )
    return helpers, modules, cost, selector, authority, assembly, proposal, options


def test_true_legacy_configuration_uses_legacy_prices_only_without_new_catalog_keys() -> None:
    _helpers, _modules_value, cost, selector = _modules()
    legacy = _legacy(cost)

    selected = selector.select_billing_tariff_prices(
        {"unrelated": True},
        day=date(2026, 8, 14),
        legacy_prices=legacy,
    )

    assert selected.prices == legacy
    assert selected.source == "legacy_options"
    assert selected.all_in_tariff_fingerprint is None
    assert selected.authority_method is None
    assert selector.has_new_tariff_catalog({"unrelated": True}) is False


def test_verified_parser_confirmation_drives_billing_from_exact_all_in_prices() -> None:
    _helpers, _modules_value, cost, selector, authority, assembly, proposal, options = _confirmed()

    selected = selector.select_billing_tariff_prices(
        options,
        day=date(2026, 8, 14),
        legacy_prices=_legacy(cost),
    )

    assert selected.source == "confirmed_all_in"
    assert selected.prices.high_rate_czk_per_kwh == assembly.all_in_vt_czk_kwh
    assert selected.prices.low_rate_czk_per_kwh == assembly.all_in_nt_czk_kwh
    assert selected.prices.fixed_monthly_czk == assembly.fixed_monthly_total_czk
    assert selected.all_in_tariff_fingerprint == proposal.all_in_tariff_fingerprint
    assert selected.authority_method is authority.AllInTariffAuthorityMethod.VERIFIED_PARSER
    assert selected.supplier == "cez"
    assert selected.product_name == "Elektřina na 3 roky"


def test_manual_user_entry_confirmation_drives_same_all_in_billing_model() -> None:
    _helpers, _modules_value, cost, selector, authority, assembly, proposal, options = _confirmed(manual=True)

    selected = selector.select_billing_tariff_prices(
        options,
        day=date(2026, 8, 14),
        legacy_prices=_legacy(cost),
    )

    assert selected.source == "confirmed_all_in"
    assert selected.prices.high_rate_czk_per_kwh == assembly.all_in_vt_czk_kwh
    assert selected.prices.low_rate_czk_per_kwh == assembly.all_in_nt_czk_kwh
    assert selected.prices.fixed_monthly_czk == assembly.fixed_monthly_total_czk
    assert selected.all_in_tariff_fingerprint == proposal.all_in_tariff_fingerprint
    assert selected.authority_method is authority.AllInTariffAuthorityMethod.MANUAL_USER_ENTRY


def test_partial_new_catalog_never_falls_back_to_stale_legacy_prices() -> None:
    _helpers, modules, cost, selector = _modules()
    contracts = modules[1]
    legacy = _legacy(cost)

    options = {
        contracts.OPTION_ELECTRICITY_CONTRACTS: [],
        "price_vt": "1.23",
        "price_nt": "1.11",
    }
    assert selector.has_new_tariff_catalog(options) is True

    with pytest.raises(LookupError, match="confirmed electricity contract"):
        selector.select_billing_tariff_prices(
            options,
            day=date(2026, 8, 14),
            legacy_prices=legacy,
        )


def test_unconfirmed_customer_targets_do_not_enable_billing_and_do_not_use_legacy() -> None:
    helpers, modules, cost, selector = _modules()
    _options, _proposal, _version = helpers._staged(modules)

    with pytest.raises(LookupError, match="confirmed electricity contract"):
        selector.select_billing_tariff_prices(
            _options,
            day=date(2026, 8, 14),
            legacy_prices=_legacy(cost),
        )


def test_confirmed_all_in_without_explicit_authority_fails_closed() -> None:
    _helpers, _modules_value, cost, selector, authority, _assembly, _proposal, options = _confirmed()
    broken = deepcopy(options)
    broken.pop(authority.OPTION_ALL_IN_TARIFF_AUTHORITIES)

    with pytest.raises(LookupError, match="all-in tariff authority not found"):
        selector.select_billing_tariff_prices(
            broken,
            day=date(2026, 8, 14),
            legacy_prices=_legacy(cost),
        )


def test_confirmed_contract_context_mismatch_cannot_select_unrelated_all_in() -> None:
    _helpers, modules, cost, selector, _authority, _assembly, _proposal, options = _confirmed()
    contracts = modules[1]
    broken = deepcopy(options)
    raw_contract = broken[contracts.OPTION_ELECTRICITY_CONTRACTS][0]
    raw_contract["product_name"] = "Jiný produkt"

    with pytest.raises(LookupError, match="confirmed all-in tariff"):
        selector.select_billing_tariff_prices(
            broken,
            day=date(2026, 8, 14),
            legacy_prices=_legacy(cost),
        )
