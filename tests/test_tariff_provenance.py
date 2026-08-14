from datetime import date
import importlib.util
from pathlib import Path
import sys
import types


def load_modules():
    for name in (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_provenance",
    ):
        sys.modules.pop(name, None)

    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    def load(name: str, path: str):
        spec = importlib.util.spec_from_file_location(name, Path(path))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    sources = load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    provenance = load(
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components/frakon_energy/tariff_provenance.py",
    )
    return sources, provenance


def _supplier_evidence(sources, provenance):
    return provenance.PriceEvidence(
        scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
        source_name="ČEZ Prodej",
        document_name="Basic – ceník elektřiny",
        source_url="https://www.cez.cz/file/edee/2026/basic.pdf",
        valid_from=date(2026, 1, 1),
        document_date=date(2025, 12, 1),
        checksum="a" * 64,
    )


def _regulated_evidence(sources, provenance):
    return provenance.PriceEvidence(
        scope=sources.PRICE_SCOPE_REGULATED,
        source_name="Regulované ceny",
        document_name="D25d 3x25A 2026",
        source_url="https://www.example-regulator.cz/2026/d25d.pdf",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        document_date=date(2025, 11, 30),
        checksum="b" * 64,
    )


def test_multisource_provenance_round_trip_and_fingerprint_are_order_independent() -> None:
    sources, provenance = load_modules()
    supplier = _supplier_evidence(sources, provenance)
    regulated = _regulated_evidence(sources, provenance)

    first = provenance.MultiSourceTariffProvenance((supplier, regulated))
    reversed_order = provenance.MultiSourceTariffProvenance((regulated, supplier))

    assert first == reversed_order
    assert first.complete_for_all_in is True
    assert first.valid_from == date(2026, 1, 1)
    assert first.valid_to == date(2026, 12, 31)
    assert first.covers_day(date(2026, 8, 14)) is True
    assert first.covers_day(date(2027, 1, 1)) is False
    assert provenance.tariff_provenance_fingerprint(first) == provenance.tariff_provenance_fingerprint(
        reversed_order
    )

    payload = first.as_dict()
    assert payload["schema_version"] == provenance.TARIFF_PROVENANCE_SCHEMA_VERSION
    restored = provenance.MultiSourceTariffProvenance.from_dict(payload)
    assert restored == first


def test_multisource_provenance_requires_both_price_scopes() -> None:
    sources, provenance = load_modules()
    supplier = _supplier_evidence(sources, provenance)
    second_supplier = provenance.PriceEvidence(
        scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
        source_name="ČEZ Prodej",
        document_name="Druhý obchodní dokument",
        source_url="https://www.cez.cz/file/edee/2026/other.pdf",
        valid_from=date(2026, 1, 1),
        checksum="c" * 64,
    )

    try:
        provenance.MultiSourceTariffProvenance((supplier, second_supplier))
    except ValueError as err:
        assert "supplier-commercial and regulated" in str(err)
    else:
        raise AssertionError("All-in provenance without regulated evidence must be rejected")


def test_multisource_provenance_rejects_duplicate_and_non_overlapping_evidence() -> None:
    sources, provenance = load_modules()
    supplier = _supplier_evidence(sources, provenance)
    regulated = _regulated_evidence(sources, provenance)

    try:
        provenance.MultiSourceTariffProvenance((supplier, regulated, regulated))
    except ValueError as err:
        assert "duplicate price evidence" in str(err)
    else:
        raise AssertionError("Duplicate provenance evidence must be rejected")

    future_regulated = provenance.PriceEvidence(
        scope=sources.PRICE_SCOPE_REGULATED,
        source_name="Regulované ceny",
        document_name="Regulace 2027",
        source_url="https://www.example-regulator.cz/2027/d25d.pdf",
        valid_from=date(2027, 1, 1),
        valid_to=date(2027, 12, 31),
        checksum="d" * 64,
    )
    supplier_2026 = provenance.PriceEvidence(
        scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
        source_name="ČEZ Prodej",
        document_name="Basic 2026",
        source_url="https://www.cez.cz/file/edee/2026/basic.pdf",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        checksum="e" * 64,
    )

    try:
        provenance.MultiSourceTariffProvenance((supplier_2026, future_regulated))
    except ValueError as err:
        assert "do not overlap" in str(err)
    else:
        raise AssertionError("Non-overlapping price evidence must be rejected")


def test_price_evidence_rejects_unsafe_url_unknown_scope_and_bad_checksum() -> None:
    sources, provenance = load_modules()

    for url in (
        "http://www.cez.cz/cenik.pdf",
        "https://user:pass@www.cez.cz/cenik.pdf",
        "https://www.cez.cz:8443/cenik.pdf",
    ):
        try:
            provenance.PriceEvidence(
                scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
                source_name="ČEZ",
                document_name="Ceník",
                source_url=url,
                valid_from=date(2026, 1, 1),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Unsafe evidence URL must be rejected")

    try:
        provenance.PriceEvidence(
            scope=sources.PRICE_SCOPE_ALL_IN,
            source_name="Unknown",
            document_name="All-in shortcut",
            source_url="https://www.example.cz/cenik.pdf",
            valid_from=date(2026, 1, 1),
        )
    except ValueError as err:
        assert "unsupported price evidence scope" in str(err)
    else:
        raise AssertionError("Composite all-in scope must not be accepted as source evidence")

    try:
        provenance.PriceEvidence(
            scope=sources.PRICE_SCOPE_REGULATED,
            source_name="Regulace",
            document_name="Ceník",
            source_url="https://www.example.cz/cenik.pdf",
            valid_from=date(2026, 1, 1),
            checksum="not-a-sha256",
        )
    except ValueError as err:
        assert "checksum" in str(err)
    else:
        raise AssertionError("Malformed evidence checksum must be rejected")
