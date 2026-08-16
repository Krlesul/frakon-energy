from dataclasses import replace
from datetime import date, datetime, timezone
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


def _fixture(*, manual=False):
    billing_helpers = _load(
        "_frakon_test_tariff_diagnostics_billing_helpers",
        "tests/test_billing_tariff_selection.py",
    )
    (
        helpers,
        modules,
        _cost,
        _selector,
        authority,
        assembly,
        proposal,
        options,
    ) = billing_helpers._confirmed(manual=manual)

    parser_preview = types.ModuleType(
        "custom_components.frakon_energy.tariff_parser_preview"
    )
    parser_preview.supplier_parser_supported = lambda supplier: (
        getattr(supplier, "value", supplier) in {"cez", "eon", "pre"}
    )
    sys.modules[parser_preview.__name__] = parser_preview

    source_watch = _load(
        "custom_components.frakon_energy.tariff_source_watch",
        "custom_components/frakon_energy/tariff_source_watch.py",
    )
    source_watch_store = _load(
        "custom_components.frakon_energy.tariff_source_watch_store",
        "custom_components/frakon_energy/tariff_source_watch_store.py",
    )
    diagnostics = _load(
        "custom_components.frakon_energy.tariff_diagnostics",
        "custom_components/frakon_energy/tariff_diagnostics.py",
    )
    all_in = modules[-2]
    persisted = next(
        item
        for item in all_in.all_in_tariffs_from_options(options)
        if all_in.all_in_tariff_fingerprint(item) == proposal.all_in_tariff_fingerprint
    )
    return (
        helpers,
        modules,
        authority,
        assembly,
        proposal,
        options,
        persisted,
        source_watch,
        source_watch_store,
        diagnostics,
    )


def _watch_for(source_watch, persisted):
    return source_watch.source_watch_from_confirmed_all_in(
        persisted,
        supplier="cez",
    )


def test_confirmed_parser_tariff_diagnostics_expose_source_parser_and_last_check() -> None:
    (
        _helpers,
        _modules,
        authority,
        assembly,
        proposal,
        options,
        persisted,
        source_watch,
        store,
        diagnostics,
    ) = _fixture()
    watch = _watch_for(source_watch, persisted)
    options = store.append_tariff_source_watch(options, watch)
    check = source_watch.tariff_source_not_modified(
        watch,
        checked_at=datetime(2026, 8, 16, 7, 0, tzinfo=timezone.utc),
        etag='"abc"',
        last_modified="Sat, 15 Aug 2026 18:00:00 GMT",
    )
    options = store.record_tariff_source_check(options, check)

    snapshot = diagnostics.build_tariff_diagnostics(
        options,
        day=date(2026, 8, 14),
    )
    payload = snapshot.as_dict()

    assert payload["contract_fingerprint"] == proposal.contract_fingerprint
    assert payload["all_in_tariff_fingerprint"] == proposal.all_in_tariff_fingerprint
    assert payload["authority_method"] == authority.AllInTariffAuthorityMethod.VERIFIED_PARSER.value
    assert payload["all_in_vt_czk_kwh"] == format(assembly.all_in_vt_czk_kwh, "f")
    assert payload["all_in_nt_czk_kwh"] == format(assembly.all_in_nt_czk_kwh, "f")
    assert payload["fixed_monthly_total_czk"] == format(
        assembly.fixed_monthly_total_czk,
        "f",
    )
    assert payload["supplier_source"]["source_url"].startswith("https://")
    assert payload["supplier_source"]["document_date"] is not None
    assert len(payload["supplier_source"]["checksum"]) == 64
    assert payload["regulated_sources"]
    assert payload["parser"] == {
        "supported": True,
        "status": diagnostics.PARSER_STATUS_VERIFIED,
    }
    assert payload["source_watch"]["binding"] == diagnostics.WATCH_BINDING_CURRENT
    assert payload["source_watch"]["registered"] is True
    assert payload["source_watch"]["etag"] == '"abc"'
    assert payload["source_watch"]["last_modified"] == "Sat, 15 Aug 2026 18:00:00 GMT"
    assert payload["source_watch"]["last_check"]["status"] == source_watch.STATUS_NOT_MODIFIED
    assert payload["source_watch"]["last_check"]["checked_at"] == "2026-08-16T07:00:00+00:00"
    assert payload["read_only"] is True
    assert payload["persistence_performed"] is False
    assert payload["activation_performed"] is False


def test_missing_watch_is_reported_without_mutating_options() -> None:
    *_, options, _persisted, _source_watch, _store, diagnostics = _fixture()
    before = dict(options)

    payload = diagnostics.build_tariff_diagnostics(
        options,
        day=date(2026, 8, 14),
    ).as_dict()

    assert options == before
    assert payload["source_watch"]["binding"] == diagnostics.WATCH_BINDING_MISSING
    assert payload["source_watch"]["registered"] is False
    assert payload["source_watch"]["last_check"] is None
    assert payload["source_watch"]["pending_sha256"] is None


def test_stale_watch_checksum_is_visible_but_never_rebound_by_diagnostics() -> None:
    (
        _helpers,
        _modules,
        _authority,
        _assembly,
        _proposal,
        options,
        persisted,
        source_watch,
        store,
        diagnostics,
    ) = _fixture()
    expected = _watch_for(source_watch, persisted)
    stale = replace(expected, active_sha256="9" * 64)
    stale_record = store.TariffSourceWatchRecord(watch=stale)
    options = dict(options)
    options[store.OPTION_TARIFF_SOURCE_WATCHES] = [stale_record.as_dict()]
    before = options[store.OPTION_TARIFF_SOURCE_WATCHES][0]["watch"]["active_sha256"]

    payload = diagnostics.build_tariff_diagnostics(
        options,
        day=date(2026, 8, 14),
    ).as_dict()

    assert payload["source_watch"]["binding"] == diagnostics.WATCH_BINDING_STALE_CHECKSUM
    assert payload["source_watch"]["registered"] is True
    assert options[store.OPTION_TARIFF_SOURCE_WATCHES][0]["watch"]["active_sha256"] == before
    assert before == "9" * 64
    assert payload["supplier_source"]["checksum"] == expected.active_sha256


def test_manual_tariff_reports_manual_authority_while_parser_capability_remains_visible() -> None:
    *_, authority, _assembly, _proposal, options, _persisted, _watch, _store, diagnostics = _fixture(
        manual=True
    )

    payload = diagnostics.build_tariff_diagnostics(
        options,
        day=date(2026, 8, 14),
    ).as_dict()

    assert payload["authority_method"] == authority.AllInTariffAuthorityMethod.MANUAL_USER_ENTRY.value
    assert payload["parser"] == {
        "supported": True,
        "status": diagnostics.PARSER_STATUS_MANUAL,
    }


def test_verified_parser_authority_without_parser_support_fails_closed() -> None:
    *_, options, _persisted, _source_watch, _store, diagnostics = _fixture()
    original = diagnostics.supplier_parser_supported
    diagnostics.supplier_parser_supported = lambda _supplier: False
    try:
        with pytest.raises(ValueError, match="without parser support"):
            diagnostics.build_tariff_diagnostics(
                options,
                day=date(2026, 8, 14),
            )
    finally:
        diagnostics.supplier_parser_supported = original
