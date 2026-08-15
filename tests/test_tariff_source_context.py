from datetime import date
import importlib.util
from pathlib import Path
import sys
import types

import pytest


def _reset_packages() -> None:
    for name in (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.tariff_source_context",
        "custom_components.frakon_energy.tariff_sources",
    ):
        sys.modules.pop(name, None)
    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package


def load_sources():
    _reset_packages()
    name = "custom_components.frakon_energy.tariff_sources"
    spec = importlib.util.spec_from_file_location(
        name,
        Path("custom_components/frakon_energy/tariff_sources.py"),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_context_then_sources():
    _reset_packages()
    context_name = "custom_components.frakon_energy.tariff_source_context"
    context_spec = importlib.util.spec_from_file_location(
        context_name,
        Path("custom_components/frakon_energy/tariff_source_context.py"),
    )
    context_module = importlib.util.module_from_spec(context_spec)
    sys.modules[context_name] = context_module
    context_spec.loader.exec_module(context_module)

    sources_name = "custom_components.frakon_energy.tariff_sources"
    sources_spec = importlib.util.spec_from_file_location(
        sources_name,
        Path("custom_components/frakon_energy/tariff_sources.py"),
    )
    sources_module = importlib.util.module_from_spec(sources_spec)
    sys.modules[sources_name] = sources_module
    sources_spec.loader.exec_module(sources_module)
    return context_module, sources_module


def test_czech_postcode_normalizes_without_location_inference() -> None:
    sources = load_sources()
    context = sources.TariffSourceResolutionContext(postcode=" 412 01 ")
    assert context.postcode == "41201"
    assert context.as_dict() == {"postcode": "41201"}
    assert context.is_empty is False


@pytest.mark.parametrize(
    "value",
    ("", "00000", "99999", "1100", "110000", "11A00", "CZ11000"),
)
def test_invalid_postcodes_fail_closed(value: str) -> None:
    sources = load_sources()
    with pytest.raises(ValueError, match="postcode"):
        sources.normalize_czech_postcode(value)


def test_source_context_rejects_unknown_fields_including_url_or_price() -> None:
    sources = load_sources()
    with pytest.raises(ValueError, match="unsupported fields"):
        sources.TariffSourceResolutionContext.from_value(
            {"postcode": "41201", "source_url": "https://attacker.invalid/file.pdf"}
        )
    with pytest.raises(ValueError, match="unsupported fields"):
        sources.TariffSourceResolutionContext.from_value(
            {"postcode": "41201", "price": "3.50"}
        )


def test_empty_context_is_explicit_and_stable() -> None:
    sources = load_sources()
    empty = sources.TariffSourceResolutionContext.from_value(None)
    assert empty.is_empty is True
    assert empty.as_dict() == {}
    assert sources.tariff_source_context_fingerprint(empty) == (
        sources.tariff_source_context_fingerprint(
            sources.TariffSourceResolutionContext()
        )
    )


def test_operational_context_has_separate_stable_fingerprint() -> None:
    sources = load_sources()
    normalized = sources.TariffSourceResolutionContext(postcode="412 01")
    same = sources.TariffSourceResolutionContext(postcode="41201")
    other = sources.TariffSourceResolutionContext(postcode="11000")
    assert sources.tariff_source_context_fingerprint(normalized) == sources.tariff_source_context_fingerprint(same)
    assert sources.tariff_source_context_fingerprint(normalized) != sources.tariff_source_context_fingerprint(other)
    assert len(sources.tariff_source_context_fingerprint(normalized)) == 64


def test_query_requires_typed_context_and_keeps_it_operational() -> None:
    sources = load_sources()
    context = sources.TariffSourceResolutionContext(postcode="41201")
    query = sources.TariffSourceQuery(
        supplier="mnd",
        product_name="Proud - Ceník Říjen 28",
        distributor="cez_distribuce",
        contract_kind="fixed",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_on=date(2026, 8, 15),
        source_context=context,
    )
    assert query.source_context is context
    with pytest.raises(ValueError, match="TariffSourceResolutionContext"):
        sources.TariffSourceQuery(
            supplier="mnd",
            product_name="Proud - Ceník Říjen 28",
            distributor="cez_distribuce",
            contract_kind="fixed",
            distribution_tariff="D25d",
            breaker_code="3x25A",
            valid_on=date(2026, 8, 15),
            source_context={"postcode": "41201"},
        )


def test_query_normalizes_independently_loaded_context_by_allowed_fields_only() -> None:
    context_module, sources = load_context_then_sources()
    external = context_module.TariffSourceResolutionContext(postcode=" 412 01 ")

    query = sources.TariffSourceQuery(
        supplier="mnd",
        product_name="Proud - Ceník Říjen 28",
        distributor="cez_distribuce",
        contract_kind="fixed",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_on=date(2026, 8, 15),
        source_context=external,
    )

    assert isinstance(query.source_context, sources.TariffSourceResolutionContext)
    assert query.source_context.as_dict() == {"postcode": "41201"}
    assert query.source_context is not external
