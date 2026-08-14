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
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components.frakon_energy.regulated_catalog",
        "custom_components.frakon_energy.regulated_proposals",
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
    catalog = _load(
        "custom_components.frakon_energy.regulated_catalog",
        "custom_components/frakon_energy/regulated_catalog.py",
    )
    proposals = _load(
        "custom_components.frakon_energy.regulated_proposals",
        "custom_components/frakon_energy/regulated_proposals.py",
    )
    return pricing, sources, regulated, provenance, catalog, proposals


def _proposal(modules, *, proposed_at=None, checksum="a" * 64, confirmed=False):
    pricing, sources, regulated, provenance, _catalog, proposals = modules
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
        ),
        fixed_components=(
            pricing.FixedPriceComponent(
                kind=pricing.PriceComponentKind.BREAKER_FIXED,
                name="Jistič",
                monthly_czk=Decimal("200.00"),
                includes_vat=False,
            ),
        ),
        source_url=source_url,
        document_date=date(2025, 11, 28),
        checksum=checksum,
        confirmed=confirmed,
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
    return proposals.RegulatedTariffProposal(
        bundle=bundle,
        evidence=evidence,
        proposed_at=proposed_at or datetime(2026, 8, 14, 17, 0, tzinfo=timezone.utc),
    )


def test_round_trip_preserves_unconfirmed_payload_and_timestamp_but_identity_ignores_time() -> None:
    modules = load_modules()
    proposals = modules[-1]
    first = _proposal(modules)
    later = _proposal(
        modules,
        proposed_at=datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc),
    )

    assert first.fingerprint == later.fingerprint
    encoded = first.as_dict()
    restored = proposals.RegulatedTariffProposal.from_dict(encoded)

    assert restored == first
    assert restored.bundle.confirmed is False
    assert encoded["proposed_at"] == "2026-08-14T17:00:00+00:00"
    assert encoded["fingerprint"] == first.fingerprint


def test_append_is_immutable_idempotent_and_keeps_first_server_timestamp() -> None:
    modules = load_modules()
    proposals = modules[-1]
    first = _proposal(modules)
    same_price_later = _proposal(
        modules,
        proposed_at=datetime(2026, 8, 15, 17, 0, tzinfo=timezone.utc),
    )
    options = {"unrelated": {"keep": True}}

    stored = proposals.append_regulated_tariff_proposal(options, first)
    duplicate = proposals.append_regulated_tariff_proposal(stored, same_price_later)

    assert duplicate == stored
    assert stored["unrelated"] == {"keep": True}
    restored = proposals.regulated_tariff_proposals_from_options(stored)
    assert restored == (first,)
    assert restored[0].proposed_at == first.proposed_at


def test_confirmation_accepts_only_existing_fingerprint_and_appends_confirmed_catalog_version() -> None:
    modules = load_modules()
    catalog = modules[-2]
    proposals = modules[-1]
    proposal = _proposal(modules)
    options = proposals.append_regulated_tariff_proposal({"unrelated": 7}, proposal)

    confirmed_options, version = proposals.confirm_regulated_tariff_proposal(
        options,
        proposal.fingerprint,
    )

    assert confirmed_options["unrelated"] == 7
    stored_proposals = proposals.regulated_tariff_proposals_from_options(confirmed_options)
    assert stored_proposals == (proposal,)
    assert stored_proposals[0].bundle.confirmed is False
    assert version.bundle.confirmed is True
    confirmed_versions = catalog.confirmed_regulated_versions_from_options(confirmed_options)
    assert confirmed_versions == (version,)

    repeated, repeated_version = proposals.confirm_regulated_tariff_proposal(
        confirmed_options,
        proposal.fingerprint,
    )
    assert repeated == confirmed_options
    assert repeated_version.fingerprint == version.fingerprint


def test_unknown_or_malformed_confirmation_fingerprint_fails_closed() -> None:
    modules = load_modules()
    proposals = modules[-1]
    proposal = _proposal(modules)
    options = proposals.append_regulated_tariff_proposal({}, proposal)

    with pytest.raises(ValueError, match="lowercase SHA-256"):
        proposals.confirm_regulated_tariff_proposal(options, "not-a-fingerprint")
    with pytest.raises(LookupError, match="proposal not found"):
        proposals.confirm_regulated_tariff_proposal(options, "0" * 64)


def test_proposal_rejects_already_confirmed_bundle_and_serialized_tampering() -> None:
    modules = load_modules()
    proposals = modules[-1]
    with pytest.raises(ValueError, match="remain unconfirmed"):
        _proposal(modules, confirmed=True)

    proposal = _proposal(modules)
    tampered = deepcopy(proposal.as_dict())
    tampered["bundle"]["variable_components"][0]["high_rate_czk_per_kwh"] = "99.99"
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        proposals.RegulatedTariffProposal.from_dict(tampered)


def test_duplicate_serialized_proposal_is_rejected_as_corrupt_options_state() -> None:
    modules = load_modules()
    proposals = modules[-1]
    record = _proposal(modules).as_dict()

    with pytest.raises(ValueError, match="duplicate regulated proposal fingerprint"):
        proposals.regulated_tariff_proposals_from_options(
            {
                proposals.OPTION_REGULATED_TARIFF_PROPOSALS: [
                    record,
                    deepcopy(record),
                ]
            }
        )
