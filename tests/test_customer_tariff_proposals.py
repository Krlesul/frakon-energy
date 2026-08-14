from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types

import pytest


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_modules():
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.pricing",
        "custom_components.frakon_energy.contracts",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components.frakon_energy.tariff_assembly",
        "custom_components.frakon_energy.regulated_catalog",
        "custom_components.frakon_energy.all_in_catalog",
        "custom_components.frakon_energy.customer_tariff_proposals",
    )
    for name in names:
        sys.modules.pop(name, None)
    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    pricing = _load(
        "custom_components.frakon_energy.pricing",
        "custom_components/frakon_energy/pricing.py",
    )
    contracts = _load(
        "custom_components.frakon_energy.contracts",
        "custom_components/frakon_energy/contracts.py",
    )
    sources = _load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    regulated = _load(
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components/frakon_energy/regulated_pricing.py",
    )
    provenance = _load(
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components/frakon_energy/tariff_provenance.py",
    )
    assembly = _load(
        "custom_components.frakon_energy.tariff_assembly",
        "custom_components/frakon_energy/tariff_assembly.py",
    )
    regulated_catalog = _load(
        "custom_components.frakon_energy.regulated_catalog",
        "custom_components/frakon_energy/regulated_catalog.py",
    )
    all_in = _load(
        "custom_components.frakon_energy.all_in_catalog",
        "custom_components/frakon_energy/all_in_catalog.py",
    )
    customer = _load(
        "custom_components.frakon_energy.customer_tariff_proposals",
        "custom_components/frakon_energy/customer_tariff_proposals.py",
    )
    return (
        pricing,
        contracts,
        sources,
        regulated,
        provenance,
        assembly,
        regulated_catalog,
        all_in,
        customer,
    )


def _regulated_version(modules, *, checksum="a" * 64):
    pricing, _contracts, sources, regulated, provenance, _assembly, catalog, _all_in, _customer = modules
    source_url = "https://eru.gov.cz/energeticky-regulacni-vestnik-182025"
    bundle = regulated.RegulatedTariffBundle(
        distributor="cez_distribuce",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        variable_components=(
            pricing.VariablePriceComponent(
                kind=pricing.PriceComponentKind.DISTRIBUTION,
                name="Distribuce",
                high_rate_czk_per_kwh=Decimal("1.1234"),
                low_rate_czk_per_kwh=Decimal("0.5678"),
                includes_vat=False,
            ),
            pricing.VariablePriceComponent(
                kind=pricing.PriceComponentKind.SYSTEM_SERVICES,
                name="Systémové služby",
                high_rate_czk_per_kwh=Decimal("0.1000"),
                low_rate_czk_per_kwh=Decimal("0.1000"),
                includes_vat=False,
            ),
            pricing.VariablePriceComponent(
                kind=pricing.PriceComponentKind.POZE,
                name="POZE",
                high_rate_czk_per_kwh=Decimal("0"),
                low_rate_czk_per_kwh=Decimal("0"),
                includes_vat=False,
            ),
            pricing.VariablePriceComponent(
                kind=pricing.PriceComponentKind.ELECTRICITY_TAX,
                name="Daň z elektřiny",
                high_rate_czk_per_kwh=Decimal("0.0283"),
                low_rate_czk_per_kwh=Decimal("0.0283"),
                includes_vat=False,
            ),
        ),
        fixed_components=(
            pricing.FixedPriceComponent(
                kind=pricing.PriceComponentKind.BREAKER_FIXED,
                name="Jistič",
                monthly_czk=Decimal("200.00"),
                includes_vat=False,
            ),
            pricing.FixedPriceComponent(
                kind=pricing.PriceComponentKind.OTHER_FIXED,
                name=regulated.NON_NETWORK_INFRASTRUCTURE_COMPONENT_NAME,
                monthly_czk=Decimal("12.87"),
                includes_vat=False,
            ),
        ),
        source_url=source_url,
        document_date=date(2025, 11, 28),
        checksum=checksum,
        confirmed=True,
    )
    evidence = (
        provenance.PriceEvidence(
            scope=sources.PRICE_SCOPE_REGULATED,
            source_name="Energetický regulační úřad",
            document_name="Cenový výměr 14/2025",
            source_url=source_url,
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
            document_date=date(2025, 11, 28),
            checksum=checksum,
        ),
    )
    return catalog.ConfirmedRegulatedTariffVersion(bundle=bundle, evidence=evidence)


def _contract(modules, *, confirmed=False):
    _pricing, contracts, *_rest = modules
    return contracts.ElectricityContract(
        supplier=contracts.Supplier.CEZ,
        distributor=contracts.Distributor.CEZ_DISTRIBUCE,
        product_name="Elektřina na 3 roky",
        contract_kind=contracts.ContractKind.FIXED,
        distribution_tariff="D25d",
        breaker=contracts.Breaker(3, 25),
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        fixation_end=date(2028, 12, 31),
        customer_confirmed=confirmed,
    )


def _assembly(modules, version):
    pricing, _contracts, sources, _regulated, provenance, assembly, *_rest = modules
    supplier_evidence = provenance.PriceEvidence(
        scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
        source_name="ČEZ Prodej",
        document_name="Elektřina na 3 roky 2026.pdf",
        source_url="https://www.cez.cz/file/edee/cenik-2026.pdf",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        document_date=date(2025, 12, 1),
        checksum="b" * 64,
    )
    multi = provenance.MultiSourceTariffProvenance(
        (supplier_evidence, *version.evidence)
    )
    commodity = pricing.VariablePriceComponent(
        kind=pricing.PriceComponentKind.COMMODITY,
        name="ČEZ – obchodní cena elektřiny",
        high_rate_czk_per_kwh=Decimal("3.960"),
        low_rate_czk_per_kwh=Decimal("3.700"),
        includes_vat=True,
    )
    supplier_fixed = pricing.FixedPriceComponent(
        kind=pricing.PriceComponentKind.SUPPLIER_FIXED,
        name="ČEZ – stálá platba dodavatele",
        monthly_czk=Decimal("130.68"),
        includes_vat=True,
    )
    return assembly.assemble_all_in_tariff(
        supplier="cez",
        product_name="Elektřina na 3 roky",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        commercial_valid_from=date(2026, 1, 1),
        commercial_valid_to=date(2026, 12, 31),
        commodity=commodity,
        supplier_fixed=supplier_fixed,
        regulated=version.bundle,
        provenance=multi,
    )


def _staged(modules, *, input_confirmed=False):
    *_prefix, regulated_catalog, _all_in, customer = modules
    version = _regulated_version(modules)
    options = regulated_catalog.append_confirmed_regulated_tariff({}, version)
    options, proposal = customer.stage_customer_tariff_proposal(
        options,
        contract=_contract(modules, confirmed=input_confirmed),
        assembly=_assembly(modules, version),
        candidate_fingerprint="c" * 64,
        regulated_version_fingerprint=version.fingerprint,
        proposed_for_day=date(2026, 8, 14),
        proposed_at=datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc),
    )
    return options, proposal, version


def test_proposal_round_trip_identity_excludes_server_timestamp() -> None:
    modules = load_modules()
    customer = modules[-1]
    options, proposal, _version = _staged(modules)
    later = customer.CustomerTariffProposal(
        contract_fingerprint=proposal.contract_fingerprint,
        all_in_tariff_fingerprint=proposal.all_in_tariff_fingerprint,
        candidate_fingerprint=proposal.candidate_fingerprint,
        regulated_version_fingerprint=proposal.regulated_version_fingerprint,
        proposed_for_day=proposal.proposed_for_day,
        proposed_at=datetime(2026, 8, 15, 20, 0, tzinfo=timezone.utc),
    )

    assert later.fingerprint == proposal.fingerprint
    restored = customer.CustomerTariffProposal.from_dict(proposal.as_dict())
    assert restored == proposal
    assert customer.customer_tariff_proposals_from_options(options) == (proposal,)


def test_stage_strips_ui_confirmation_and_persists_only_unconfirmed_customer_targets() -> None:
    modules = load_modules()
    contracts = modules[1]
    all_in = modules[-2]
    options, proposal, _version = _staged(modules, input_confirmed=True)

    stored_contract = next(
        item
        for item in contracts.contracts_from_options(options)
        if contracts.contract_fingerprint(item) == proposal.contract_fingerprint
    )
    stored_all_in = next(
        item
        for item in all_in.all_in_tariffs_from_options(options)
        if all_in.all_in_tariff_fingerprint(item) == proposal.all_in_tariff_fingerprint
    )
    assert stored_contract.customer_confirmed is False
    assert stored_all_in.confirmed is False


def test_stage_is_idempotent_and_preserves_first_proposal_timestamp() -> None:
    modules = load_modules()
    customer = modules[-1]
    options, proposal, version = _staged(modules)
    repeated, repeated_proposal = customer.stage_customer_tariff_proposal(
        options,
        contract=_contract(modules),
        assembly=_assembly(modules, version),
        candidate_fingerprint="c" * 64,
        regulated_version_fingerprint=version.fingerprint,
        proposed_for_day=date(2026, 8, 14),
        proposed_at=datetime(2026, 8, 15, 20, 0, tzinfo=timezone.utc),
    )

    assert repeated == options
    assert repeated_proposal.fingerprint == proposal.fingerprint
    stored = customer.customer_tariff_proposals_from_options(repeated)
    assert stored[0].proposed_at == proposal.proposed_at


def test_confirm_atomically_confirms_linked_contract_and_all_in_and_keeps_proposal_history() -> None:
    modules = load_modules()
    contracts = modules[1]
    all_in = modules[-2]
    customer = modules[-1]
    options, proposal, _version = _staged(modules)

    confirmed, returned = customer.confirm_customer_tariff_proposal(
        options,
        proposal.fingerprint,
    )

    assert returned == proposal
    assert customer.customer_tariff_proposals_from_options(confirmed) == (proposal,)
    stored_contract = next(
        item
        for item in contracts.contracts_from_options(confirmed)
        if contracts.contract_fingerprint(item) == proposal.contract_fingerprint
    )
    stored_all_in = next(
        item
        for item in all_in.all_in_tariffs_from_options(confirmed)
        if all_in.all_in_tariff_fingerprint(item) == proposal.all_in_tariff_fingerprint
    )
    assert stored_contract.customer_confirmed is True
    assert stored_all_in.confirmed is True

    repeated, _ = customer.confirm_customer_tariff_proposal(
        confirmed,
        proposal.fingerprint,
    )
    assert repeated == confirmed


def test_stage_requires_existing_confirmed_regulator_and_matching_provenance() -> None:
    modules = load_modules()
    customer = modules[-1]
    version = _regulated_version(modules)

    with pytest.raises(LookupError, match="regulator target was not found"):
        customer.stage_customer_tariff_proposal(
            {},
            contract=_contract(modules),
            assembly=_assembly(modules, version),
            candidate_fingerprint="c" * 64,
            regulated_version_fingerprint=version.fingerprint,
            proposed_for_day=date(2026, 8, 14),
            proposed_at=datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc),
        )


def test_confirm_corrupt_link_fails_before_either_target_can_be_confirmed() -> None:
    modules = load_modules()
    contracts = modules[1]
    all_in = modules[-2]
    customer = modules[-1]
    options, proposal, _version = _staged(modules)
    corrupt = deepcopy(options)
    raw = corrupt[customer.OPTION_CUSTOMER_TARIFF_PROPOSALS][0]
    raw["regulated_version_fingerprint"] = "0" * 64
    rebuilt = customer.CustomerTariffProposal(
        contract_fingerprint=raw["contract_fingerprint"],
        all_in_tariff_fingerprint=raw["all_in_tariff_fingerprint"],
        candidate_fingerprint=raw["candidate_fingerprint"],
        regulated_version_fingerprint=raw["regulated_version_fingerprint"],
        proposed_for_day=date.fromisoformat(raw["proposed_for_day"]),
        proposed_at=datetime.fromisoformat(raw["proposed_at"]),
    )
    raw["fingerprint"] = rebuilt.fingerprint

    with pytest.raises(LookupError, match="regulator target was not found"):
        customer.confirm_customer_tariff_proposal(corrupt, rebuilt.fingerprint)

    original_contract = next(
        item
        for item in contracts.contracts_from_options(options)
        if contracts.contract_fingerprint(item) == proposal.contract_fingerprint
    )
    original_all_in = next(
        item
        for item in all_in.all_in_tariffs_from_options(options)
        if all_in.all_in_tariff_fingerprint(item) == proposal.all_in_tariff_fingerprint
    )
    assert original_contract.customer_confirmed is False
    assert original_all_in.confirmed is False


def test_unknown_malformed_and_tampered_proposal_fail_closed() -> None:
    modules = load_modules()
    customer = modules[-1]
    options, proposal, _version = _staged(modules)

    with pytest.raises(ValueError, match="lowercase SHA-256"):
        customer.confirm_customer_tariff_proposal(options, "bad")
    with pytest.raises(LookupError, match="proposal not found"):
        customer.confirm_customer_tariff_proposal(options, "0" * 64)

    tampered = proposal.as_dict()
    tampered["candidate_fingerprint"] = "d" * 64
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        customer.CustomerTariffProposal.from_dict(tampered)
