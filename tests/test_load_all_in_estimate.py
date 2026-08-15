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


def _fixture(*, manual=False):
    helpers = _load(
        "_frakon_test_load_all_in_customer_helpers",
        "tests/test_customer_tariff_proposals.py",
    )
    modules = helpers.load_modules()
    _load(
        "custom_components.frakon_energy.cost",
        "custom_components/frakon_energy/cost.py",
    )
    _load(
        "custom_components.frakon_energy.billing_tariff_selection",
        "custom_components/frakon_energy/billing_tariff_selection.py",
    )
    estimate = _load(
        "custom_components.frakon_energy.load_all_in_estimate",
        "custom_components/frakon_energy/load_all_in_estimate.py",
    )
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
        proposed_at=datetime(2026, 8, 15, 19, 30, tzinfo=timezone.utc),
        authority_method=(
            authority.AllInTariffAuthorityMethod.MANUAL_USER_ENTRY
            if manual
            else authority.AllInTariffAuthorityMethod.VERIFIED_PARSER
        ),
    )
    options, _ = customer.confirm_customer_tariff_proposal(options, proposal.fingerprint)
    return estimate, authority, assembly, proposal, options


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def test_ev_estimate_uses_confirmed_all_in_vt_nt_and_excludes_fixed_monthly() -> None:
    estimate, authority, assembly, proposal, options = _fixture()

    result = estimate.build_confirmed_all_in_load_estimate(
        options,
        starts_at="2026-08-14T20:00:00+02:00",
        ends_at="2026-08-14T22:00:00+02:00",
        power_kw="11",
    )

    energy = Decimal("22")
    assert result["available"] is True
    assert result["source"] == "confirmed_all_in"
    assert result["estimated_energy_kwh"] == 22.0
    assert Decimal(str(result["vt_cost_czk"])) == _money(
        energy * assembly.all_in_vt_czk_kwh
    )
    assert Decimal(str(result["nt_cost_czk"])) == _money(
        energy * assembly.all_in_nt_czk_kwh
    )
    assert Decimal(str(result["vt_average_czk_kwh"])) == assembly.all_in_vt_czk_kwh.quantize(Decimal("0.000001"))
    assert Decimal(str(result["nt_average_czk_kwh"])) == assembly.all_in_nt_czk_kwh.quantize(Decimal("0.000001"))
    assert result["fixed_monthly_excluded"] is True
    assert result["tariffs"][0]["all_in_tariff_fingerprint"] == proposal.all_in_tariff_fingerprint
    assert result["tariffs"][0]["authority_method"] == authority.AllInTariffAuthorityMethod.VERIFIED_PARSER.value


def test_manual_user_entry_tariff_is_valid_authority_for_boiler_estimate() -> None:
    estimate, authority, assembly, proposal, options = _fixture(manual=True)

    result = estimate.build_confirmed_all_in_load_estimate(
        options,
        starts_at="2026-08-14T12:00:00+02:00",
        ends_at="2026-08-14T13:30:00+02:00",
        power_kw="2",
    )

    assert result["estimated_energy_kwh"] == 3.0
    assert result["tariffs"][0]["all_in_tariff_fingerprint"] == proposal.all_in_tariff_fingerprint
    assert result["tariffs"][0]["authority_method"] == authority.AllInTariffAuthorityMethod.MANUAL_USER_ENTRY.value
    assert Decimal(str(result["vt_cost_czk"])) == _money(
        Decimal("3") * assembly.all_in_vt_czk_kwh
    )


def test_overnight_load_resolves_tariff_for_each_calendar_day() -> None:
    estimate, _authority, _assembly, proposal, options = _fixture()

    result = estimate.build_confirmed_all_in_load_estimate(
        options,
        starts_at="2026-08-14T23:30:00+02:00",
        ends_at="2026-08-15T00:30:00+02:00",
        power_kw="2",
    )

    assert result["estimated_energy_kwh"] == 2.0
    assert [item["day"] for item in result["tariffs"]] == ["2026-08-14", "2026-08-15"]
    assert [item["energy_kwh"] for item in result["tariffs"]] == [1.0, 1.0]
    assert {
        item["all_in_tariff_fingerprint"] for item in result["tariffs"]
    } == {proposal.all_in_tariff_fingerprint}


def test_unconfirmed_or_legacy_only_tariff_cannot_claim_all_in_load_estimate() -> None:
    estimate, _authority, _assembly, _proposal, _options = _fixture()

    with pytest.raises(LookupError):
        estimate.build_confirmed_all_in_load_estimate(
            {},
            starts_at="2026-08-14T20:00:00+02:00",
            ends_at="2026-08-14T21:00:00+02:00",
            power_kw="11",
        )


def test_unavailable_payload_never_claims_a_price() -> None:
    estimate, *_rest = _fixture()

    assert estimate.unavailable_all_in_load_estimate() == {
        "available": False,
        "source": "confirmed_all_in",
        "fixed_monthly_excluded": True,
        "reason": "confirmed_customer_all_in_unavailable",
    }
