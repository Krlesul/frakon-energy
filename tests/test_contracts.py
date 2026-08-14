from datetime import date
import importlib.util
from pathlib import Path
import sys


def load_contracts():
    path = Path("custom_components/frakon_energy/contracts.py")
    spec = importlib.util.spec_from_file_location("frakon_energy_contracts", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_contract_builds_tariff_lookup_key() -> None:
    contracts = load_contracts()
    contract = contracts.ElectricityContract(
        supplier=contracts.Supplier.CEZ,
        distributor=contracts.Distributor.CEZ_DISTRIBUCE,
        product_name="Elektřina na 3 roky",
        contract_kind=contracts.ContractKind.FIXED,
        distribution_tariff="d25D",
        breaker=contracts.Breaker(3, 25),
        valid_from=date(2026, 1, 27),
        fixation_end=date(2029, 1, 26),
        customer_confirmed=True,
    )

    key = contracts.lookup_key(contract, date(2026, 8, 5))

    assert key.supplier == contracts.Supplier.CEZ
    assert key.distributor == contracts.Distributor.CEZ_DISTRIBUCE
    assert key.distribution_tariff == "D25d"
    assert key.breaker_code == "3x25A"
    assert contract.days_until_fixation_end(date(2026, 8, 5)) == 905


def test_contract_round_trip_preserves_wizard_selection() -> None:
    contracts = load_contracts()
    contract = contracts.ElectricityContract(
        supplier=contracts.Supplier.EON,
        distributor=contracts.Distributor.EG_D,
        product_name="Variant PRO na 2 roky",
        contract_kind=contracts.ContractKind.FIXED,
        distribution_tariff="D25d",
        breaker=contracts.Breaker(3, 25),
        valid_from=date(2026, 9, 1),
        valid_to=date(2028, 8, 31),
        fixation_end=date(2028, 8, 31),
        customer_confirmed=True,
    )

    payload = contract.as_dict()
    assert payload["schema_version"] == contracts.CONTRACT_SCHEMA_VERSION
    assert payload["breaker"] == {"phases": 3, "amperes": 25}
    assert payload["distribution_tariff"] == "D25d"
    assert contracts.ElectricityContract.from_dict(payload) == contract


def test_latest_overlapping_contract_wins() -> None:
    contracts = load_contracts()
    breaker = contracts.Breaker(3, 25)
    old = contracts.ElectricityContract(
        contracts.Supplier.CEZ,
        contracts.Distributor.CEZ_DISTRIBUCE,
        "Starý produkt",
        contracts.ContractKind.INDEFINITE,
        "D25d",
        breaker,
        date(2025, 1, 1),
        date(2026, 6, 30),
    )
    new = contracts.ElectricityContract(
        contracts.Supplier.EON,
        contracts.Distributor.CEZ_DISTRIBUCE,
        "Nový fix",
        contracts.ContractKind.FIXED,
        "D25d",
        breaker,
        date(2026, 6, 1),
        fixation_end=date(2028, 5, 31),
    )

    assert contracts.select_contract_for_day((old, new), date(2026, 6, 15)) is new


def test_confirmed_selector_never_activates_newer_unconfirmed_contract() -> None:
    contracts = load_contracts()
    breaker = contracts.Breaker(3, 25)
    old = contracts.ElectricityContract(
        contracts.Supplier.CEZ,
        contracts.Distributor.CEZ_DISTRIBUCE,
        "Potvrzený produkt",
        contracts.ContractKind.INDEFINITE,
        "D25d",
        breaker,
        date(2026, 1, 1),
        customer_confirmed=True,
    )
    suggested = contracts.ElectricityContract(
        contracts.Supplier.EON,
        contracts.Distributor.CEZ_DISTRIBUCE,
        "Novější návrh",
        contracts.ContractKind.FIXED,
        "D25d",
        breaker,
        date(2026, 6, 1),
        fixation_end=date(2028, 5, 31),
    )

    assert contracts.select_contract_for_day((old, suggested), date(2026, 8, 1)) is suggested
    assert contracts.select_confirmed_contract_for_day(
        (old, suggested), date(2026, 8, 1)
    ) is old


def test_fixed_contract_requires_fixation_end() -> None:
    contracts = load_contracts()
    try:
        contracts.ElectricityContract(
            contracts.Supplier.MND,
            contracts.Distributor.EG_D,
            "Fix",
            contracts.ContractKind.FIXED,
            "D02d",
            contracts.Breaker(3, 20),
            date(2026, 1, 1),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Fixed contract without fixation end must be rejected")


def _contract(contracts, product: str, valid_from: date, *, confirmed: bool = False):
    return contracts.ElectricityContract(
        supplier=contracts.Supplier.CEZ,
        distributor=contracts.Distributor.CEZ_DISTRIBUCE,
        product_name=product,
        contract_kind=contracts.ContractKind.INDEFINITE,
        distribution_tariff="D25d",
        breaker=contracts.Breaker(3, 25),
        valid_from=valid_from,
        customer_confirmed=confirmed,
    )


def test_contract_options_append_preserves_history_and_is_idempotent() -> None:
    contracts = load_contracts()
    old = _contract(contracts, "Starý produkt", date(2026, 1, 1))
    new = _contract(contracts, "Nový produkt", date(2026, 6, 1))

    options = {"unrelated": {"keep": True}}
    options = contracts.append_electricity_contract(options, old)
    once = options
    options = contracts.append_electricity_contract(options, old)
    assert options == once
    assert options["unrelated"] == {"keep": True}
    assert len(contracts.contracts_from_options(options)) == 1

    options = contracts.append_electricity_contract(options, new)
    stored = contracts.contracts_from_options(options)
    assert [item.product_name for item in stored] == ["Starý produkt", "Nový produkt"]
    assert contracts.contract_fingerprint(stored[0]) != contracts.contract_fingerprint(stored[1])


def test_contract_confirmation_preserves_identity_and_controls_active_selection() -> None:
    contracts = load_contracts()
    old = _contract(contracts, "Starý produkt", date(2026, 1, 1))
    new = _contract(contracts, "Nový produkt", date(2026, 6, 1))
    old_fingerprint = contracts.contract_fingerprint(old)
    new_fingerprint = contracts.contract_fingerprint(new)

    options = contracts.append_electricity_contract({}, old)
    options = contracts.append_electricity_contract(options, new)
    options = contracts.confirm_electricity_contract(options, old_fingerprint)

    stored = contracts.contracts_from_options(options)
    assert stored[0].customer_confirmed is True
    assert stored[1].customer_confirmed is False
    assert contracts.contract_fingerprint(stored[0]) == old_fingerprint
    assert contracts.confirmed_contract_from_options(
        options, date(2026, 8, 1)
    ).product_name == "Starý produkt"

    options = contracts.confirm_electricity_contract(options, new_fingerprint)
    stored = contracts.contracts_from_options(options)
    assert contracts.contract_fingerprint(stored[1]) == new_fingerprint
    assert contracts.confirmed_contract_from_options(
        options, date(2026, 8, 1)
    ).product_name == "Nový produkt"


def test_contract_options_reject_duplicate_identity_and_unknown_confirmation() -> None:
    contracts = load_contracts()
    contract = _contract(contracts, "Produkt", date(2026, 1, 1))
    duplicate_options = {
        contracts.OPTION_ELECTRICITY_CONTRACTS: [contract.as_dict(), contract.as_dict()]
    }

    try:
        contracts.contracts_from_options(duplicate_options)
    except ValueError as err:
        assert "duplicate electricity contract fingerprint" in str(err)
    else:
        raise AssertionError("Duplicate stored contract identity must be rejected")

    try:
        contracts.confirm_electricity_contract({}, "0" * 64)
    except LookupError:
        pass
    else:
        raise AssertionError("Unknown contract confirmation target must be rejected")
