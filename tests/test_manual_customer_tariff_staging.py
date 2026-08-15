from copy import deepcopy
from datetime import date, datetime, timezone
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
        "_frakon_test_customer_tariff_proposal_helpers",
        "tests/test_customer_tariff_proposals.py",
    )
    modules = helpers.load_modules()
    regulated_catalog = modules[-3]
    customer = modules[-1]
    authority = sys.modules["custom_components.frakon_energy.all_in_authority"]
    version = helpers._regulated_version(modules)
    options = regulated_catalog.append_confirmed_regulated_tariff({}, version)
    return helpers, modules, customer, authority, version, options


def test_server_internal_manual_authority_is_persisted_and_survives_confirmation() -> None:
    helpers, modules, customer, authority, version, options = _fixture()

    staged, proposal = customer.stage_customer_tariff_proposal(
        options,
        contract=helpers._contract(modules),
        assembly=helpers._assembly(modules, version),
        candidate_fingerprint="c" * 64,
        regulated_version_fingerprint=version.fingerprint,
        proposed_for_day=date(2026, 8, 14),
        proposed_at=datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc),
        authority_method=authority.AllInTariffAuthorityMethod.MANUAL_USER_ENTRY,
    )

    record = authority.all_in_tariff_authority_from_options(
        staged,
        proposal.all_in_tariff_fingerprint,
    )
    assert record.method is authority.AllInTariffAuthorityMethod.MANUAL_USER_ENTRY

    confirmed, returned = customer.confirm_customer_tariff_proposal(
        staged,
        proposal.fingerprint,
    )
    assert returned == proposal
    assert authority.all_in_tariff_authority_from_options(
        confirmed,
        proposal.all_in_tariff_fingerprint,
    ).method is authority.AllInTariffAuthorityMethod.MANUAL_USER_ENTRY


def test_invalid_internal_authority_fails_without_mutating_input_options() -> None:
    helpers, modules, customer, _authority, version, options = _fixture()
    before = deepcopy(options)

    with pytest.raises(ValueError, match="unsupported all-in tariff authority method"):
        customer.stage_customer_tariff_proposal(
            options,
            contract=helpers._contract(modules),
            assembly=helpers._assembly(modules, version),
            candidate_fingerprint="c" * 64,
            regulated_version_fingerprint=version.fingerprint,
            proposed_for_day=date(2026, 8, 14),
            proposed_at=datetime(2026, 8, 15, 18, 5, tzinfo=timezone.utc),
            authority_method="client_selected_magic",
        )

    assert options == before
    assert customer.OPTION_CUSTOMER_TARIFF_PROPOSALS not in options
