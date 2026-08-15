from datetime import date, datetime, timezone
import importlib.util
from pathlib import Path
import sys
import types

import pytest


DOCUMENT_UUID = "12345678-1234-4234-8234-123456789abc"
OFFICIAL_URL = f"https://prod.mnd.cz/documents/view/{DOCUMENT_UUID}"
CONTEXT_FINGERPRINT = "a" * 64
DOCUMENT_SHA256 = "b" * 64


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
        "custom_components.frakon_energy.providers",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.providers.mnd_tariffs",
        "custom_components.frakon_energy.providers.mnd_confirmed_source_resolver",
        "custom_components.frakon_energy.providers.mnd_source_proposals",
    )
    for name in names:
        sys.modules.pop(name, None)
    for name in (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.providers",
    ):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    _load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    _load(
        "custom_components.frakon_energy.providers.mnd_tariffs",
        "custom_components/frakon_energy/providers/mnd_tariffs.py",
    )
    resolver = _load(
        "custom_components.frakon_energy.providers.mnd_confirmed_source_resolver",
        "custom_components/frakon_energy/providers/mnd_confirmed_source_resolver.py",
    )
    proposals = _load(
        "custom_components.frakon_energy.providers.mnd_source_proposals",
        "custom_components/frakon_energy/providers/mnd_source_proposals.py",
    )
    return resolver, proposals


def _proposal(
    proposals,
    *,
    proposed_at: datetime = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc),
):
    return proposals.MndSourceProposal(
        source_context_fingerprint=CONTEXT_FINGERPRINT,
        product_name="Proud - Ceník Říjen 28",
        distributor="cez_distribuce",
        contract_kind="fixed",
        source_url=OFFICIAL_URL,
        valid_from=date(2026, 6, 11),
        valid_to=date(2028, 10, 31),
        document_date=None,
        document_sha256=DOCUMENT_SHA256,
        proposed_at=proposed_at,
    )


def test_proposal_roundtrip_contains_no_prices_or_raw_postcode() -> None:
    _resolver, proposals = load_modules()
    proposal = _proposal(proposals)

    payload = proposal.as_dict()
    restored = proposals.MndSourceProposal.from_dict(payload)

    assert restored == proposal
    assert payload["source_context_fingerprint"] == CONTEXT_FINGERPRINT
    assert payload["document_sha256"] == DOCUMENT_SHA256
    assert "postcode" not in payload
    assert not any("price" in key or "czk" in key for key in payload)
    assert set(payload) == {
        "schema_version",
        "fingerprint",
        "source_context_fingerprint",
        "product_name",
        "distributor",
        "contract_kind",
        "source_url",
        "valid_from",
        "valid_to",
        "document_date",
        "document_sha256",
        "proposed_at",
    }


def test_proposal_fingerprint_ignores_timestamp_and_append_is_idempotent() -> None:
    _resolver, proposals = load_modules()
    first = _proposal(proposals)
    repeated = _proposal(
        proposals,
        proposed_at=datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc),
    )

    assert first.fingerprint == repeated.fingerprint
    once = proposals.append_mnd_source_proposal({}, first)
    twice = proposals.append_mnd_source_proposal(once, repeated)

    assert twice == once
    assert proposals.mnd_source_proposals_from_options(twice) == (first,)


def test_corrupt_proposal_fingerprint_and_duplicate_history_fail_closed() -> None:
    _resolver, proposals = load_modules()
    proposal = _proposal(proposals)
    corrupt = proposal.as_dict()
    corrupt["fingerprint"] = "0" * 64
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        proposals.MndSourceProposal.from_dict(corrupt)

    duplicate_options = {
        proposals.MND_SOURCE_PROPOSALS_OPTION: [proposal.as_dict(), proposal.as_dict()]
    }
    with pytest.raises(ValueError, match="duplicate MND source proposal fingerprint"):
        proposals.mnd_source_proposals_from_options(duplicate_options)


def test_confirmation_accepts_only_stored_fingerprint_and_preserves_proposal_history() -> None:
    resolver, proposals = load_modules()
    proposal = _proposal(proposals)
    staged = proposals.append_mnd_source_proposal({}, proposal)
    confirmed_at = datetime(2026, 8, 15, 11, 0, tzinfo=timezone.utc)

    updated, resolution = proposals.confirm_mnd_source_proposal(
        staged,
        proposal.fingerprint,
        confirmed_at=confirmed_at,
    )

    assert resolution.source_context_fingerprint == CONTEXT_FINGERPRINT
    assert resolution.product_name == "Proud - Ceník Říjen 28"
    assert resolution.source_url == OFFICIAL_URL
    assert resolution.document_sha256 == DOCUMENT_SHA256
    assert resolution.confirmed_at == confirmed_at
    assert proposals.mnd_source_proposals_from_options(updated) == (proposal,)
    confirmed = resolver.confirmed_mnd_source_resolutions_from_options(updated)
    assert confirmed == (resolution,)


def test_unknown_or_malformed_confirmation_fingerprint_never_mutates_options() -> None:
    resolver, proposals = load_modules()
    proposal = _proposal(proposals)
    staged = proposals.append_mnd_source_proposal({"keep": "value"}, proposal)

    with pytest.raises(LookupError, match="MND source proposal not found"):
        proposals.confirm_mnd_source_proposal(
            staged,
            "c" * 64,
            confirmed_at=datetime(2026, 8, 15, 11, 0, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="proposal_fingerprint"):
        proposals.confirm_mnd_source_proposal(
            staged,
            "not-a-fingerprint",
            confirmed_at=datetime(2026, 8, 15, 11, 0, tzinfo=timezone.utc),
        )
    assert staged["keep"] == "value"
    assert resolver.MND_CONFIRMED_SOURCE_RESOLUTIONS_OPTION not in staged


def test_repeated_confirmation_has_no_confirmed_history_write_churn() -> None:
    resolver, proposals = load_modules()
    proposal = _proposal(proposals)
    staged = proposals.append_mnd_source_proposal({}, proposal)
    first, _ = proposals.confirm_mnd_source_proposal(
        staged,
        proposal.fingerprint,
        confirmed_at=datetime(2026, 8, 15, 11, 0, tzinfo=timezone.utc),
    )
    second, resolution = proposals.confirm_mnd_source_proposal(
        first,
        proposal.fingerprint,
        confirmed_at=datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc),
    )

    assert second == first
    assert len(resolver.confirmed_mnd_source_resolutions_from_options(second)) == 1
    assert resolver.confirmed_mnd_source_resolution_fingerprint(resolution) == (
        resolver.confirmed_mnd_source_resolution_fingerprint(
            resolver.confirmed_mnd_source_resolutions_from_options(second)[0]
        )
    )


def test_proposal_reuses_fixed_product_public_validity_and_official_source_rules() -> None:
    _resolver, proposals = load_modules()
    with pytest.raises(ValueError, match="public product evidence"):
        proposals.MndSourceProposal(
            source_context_fingerprint=CONTEXT_FINGERPRINT,
            product_name="Proud - Ceník Říjen 28",
            distributor="cez_distribuce",
            contract_kind="fixed",
            source_url=OFFICIAL_URL,
            valid_from=date(2026, 6, 11),
            valid_to=date(2028, 12, 31),
            document_date=None,
            document_sha256=DOCUMENT_SHA256,
            proposed_at=datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="official mnd.cz host"):
        proposals.MndSourceProposal(
            source_context_fingerprint=CONTEXT_FINGERPRINT,
            product_name="Proud - Ceník Říjen 28",
            distributor="cez_distribuce",
            contract_kind="fixed",
            source_url=f"https://example.com/documents/view/{DOCUMENT_UUID}",
            valid_from=date(2026, 6, 11),
            valid_to=date(2028, 10, 31),
            document_date=None,
            document_sha256=DOCUMENT_SHA256,
            proposed_at=datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc),
        )
