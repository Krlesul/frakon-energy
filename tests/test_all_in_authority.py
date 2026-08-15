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
    helpers = _load("_frakon_test_all_in_catalog_helpers", "tests/test_all_in_catalog.py")
    modules = helpers.load_modules()
    catalog = modules[-1]
    assembly = helpers._assembly(modules)
    options = catalog.append_all_in_tariff({"unrelated": {"keep": True}}, assembly)
    item = catalog.all_in_tariffs_from_options(options)[0]
    fingerprint = catalog.all_in_tariff_fingerprint(item)
    sys.modules.pop("custom_components.frakon_energy.all_in_authority", None)
    authority = _load(
        "custom_components.frakon_energy.all_in_authority",
        "custom_components/frakon_energy/all_in_authority.py",
    )
    return catalog, authority, options, fingerprint


def test_authority_round_trip_supports_only_explicit_methods() -> None:
    _catalog, authority, _options, fingerprint = _fixture()

    for method in authority.AllInTariffAuthorityMethod:
        record = authority.AllInTariffAuthority(
            all_in_tariff_fingerprint=fingerprint,
            method=method,
        )
        assert authority.AllInTariffAuthority.from_dict(record.as_dict()) == record
        assert record.as_dict()["method"] == method.value

    with pytest.raises(ValueError, match="unsupported all-in tariff authority method"):
        authority.AllInTariffAuthority(
            all_in_tariff_fingerprint=fingerprint,
            method="trusted_magic",
        )


def test_append_requires_existing_all_in_target_and_preserves_tariff_identity() -> None:
    catalog, authority, options, fingerprint = _fixture()
    before = catalog.all_in_tariffs_from_options(options)[0]

    updated = authority.append_all_in_tariff_authority(
        options,
        all_in_fingerprint=fingerprint,
        method=authority.AllInTariffAuthorityMethod.VERIFIED_PARSER,
    )

    after = catalog.all_in_tariffs_from_options(updated)[0]
    assert catalog.all_in_tariff_fingerprint(after) == fingerprint
    assert after == before
    assert after.confirmed is False
    assert updated["unrelated"] == {"keep": True}
    assert authority.all_in_tariff_authority_from_options(
        updated,
        fingerprint,
    ).method is authority.AllInTariffAuthorityMethod.VERIFIED_PARSER

    with pytest.raises(LookupError, match="all-in tariff target not found"):
        authority.append_all_in_tariff_authority(
            {},
            all_in_fingerprint="0" * 64,
            method=authority.AllInTariffAuthorityMethod.MANUAL_USER_ENTRY,
        )


def test_authority_append_is_idempotent_but_method_is_immutable() -> None:
    _catalog, authority, options, fingerprint = _fixture()
    once = authority.append_all_in_tariff_authority(
        options,
        all_in_fingerprint=fingerprint,
        method="manual_user_entry",
    )
    twice = authority.append_all_in_tariff_authority(
        once,
        all_in_fingerprint=fingerprint,
        method=authority.AllInTariffAuthorityMethod.MANUAL_USER_ENTRY,
    )

    assert twice == once
    with pytest.raises(
        ValueError,
        match="authority is immutable and cannot change method",
    ):
        authority.append_all_in_tariff_authority(
            once,
            all_in_fingerprint=fingerprint,
            method=authority.AllInTariffAuthorityMethod.VERIFIED_PARSER,
        )
    assert authority.all_in_tariff_authority_from_options(
        once,
        fingerprint,
    ).method is authority.AllInTariffAuthorityMethod.MANUAL_USER_ENTRY


def test_reload_rejects_duplicate_corrupt_and_unknown_authority_records() -> None:
    _catalog, authority, options, fingerprint = _fixture()
    record = authority.AllInTariffAuthority(
        all_in_tariff_fingerprint=fingerprint,
        method=authority.AllInTariffAuthorityMethod.VERIFIED_PARSER,
    ).as_dict()

    duplicate = dict(options)
    duplicate[authority.OPTION_ALL_IN_TARIFF_AUTHORITIES] = [record, dict(record)]
    with pytest.raises(ValueError, match="duplicate all-in tariff authority target"):
        authority.all_in_tariff_authorities_from_options(duplicate)

    corrupt = dict(options)
    corrupt[authority.OPTION_ALL_IN_TARIFF_AUTHORITIES] = [
        {**record, "schema_version": 999}
    ]
    with pytest.raises(ValueError, match="unsupported all-in tariff authority schema"):
        authority.all_in_tariff_authorities_from_options(corrupt)

    invalid_method = dict(options)
    invalid_method[authority.OPTION_ALL_IN_TARIFF_AUTHORITIES] = [
        {**record, "method": "automatic_guess"}
    ]
    with pytest.raises(ValueError, match="unsupported all-in tariff authority method"):
        authority.all_in_tariff_authorities_from_options(invalid_method)

    with pytest.raises(LookupError, match="all-in tariff authority not found"):
        authority.all_in_tariff_authority_from_options(options, fingerprint)

    with pytest.raises(ValueError, match="lowercase SHA-256"):
        authority.all_in_tariff_authority_from_options(options, "not-a-fingerprint")
