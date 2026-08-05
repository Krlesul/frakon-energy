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
