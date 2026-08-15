import importlib.util
from pathlib import Path
import sys

import pytest


def load_module():
    name = "custom_components.frakon_energy.tariff_source_context"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        name,
        Path("custom_components/frakon_energy/tariff_source_context.py"),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_czech_postcode_normalizes_without_location_inference() -> None:
    context = load_module()

    assert context.normalize_czech_postcode("110 00") == "11000"
    assert context.TariffSourceResolutionContext(postcode=" 110 00 ").as_dict() == {
        "postcode": "11000"
    }


@pytest.mark.parametrize(
    "value",
    ("", "00000", "99999", "1100", "110000", "11A00", "CZ11000"),
)
def test_invalid_postcodes_fail_closed(value: str) -> None:
    context = load_module()

    with pytest.raises(ValueError, match="postcode"):
        context.normalize_czech_postcode(value)


def test_source_context_rejects_unknown_fields() -> None:
    context = load_module()

    with pytest.raises(ValueError, match="unsupported fields"):
        context.TariffSourceResolutionContext.from_value(
            {"postcode": "11000", "price": "3.50"}
        )


def test_empty_context_is_explicit_and_stable() -> None:
    context = load_module()

    empty = context.TariffSourceResolutionContext.from_value(None)
    assert empty.is_empty is True
    assert empty.as_dict() == {}
    assert context.tariff_source_context_fingerprint(empty) == (
        context.tariff_source_context_fingerprint(
            context.TariffSourceResolutionContext()
        )
    )


def test_operational_context_has_separate_fingerprint() -> None:
    context = load_module()

    prague = context.TariffSourceResolutionContext(postcode="11000")
    brno = context.TariffSourceResolutionContext(postcode="60200")

    assert context.tariff_source_context_fingerprint(prague) != (
        context.tariff_source_context_fingerprint(brno)
    )
    assert len(context.tariff_source_context_fingerprint(prague)) == 64
