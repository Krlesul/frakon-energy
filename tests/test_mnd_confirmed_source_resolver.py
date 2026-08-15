from datetime import date, datetime, timezone
import hashlib
import importlib.util
from pathlib import Path
import sys
import types

import pytest


DOCUMENT_UUID = "12345678-1234-4234-8234-123456789abc"
OTHER_DOCUMENT_UUID = "abcdefab-cdef-4abc-8def-abcdefabcdef"
OFFICIAL_URL = f"https://prod.mnd.cz/documents/view/{DOCUMENT_UUID}"
OTHER_OFFICIAL_URL = f"https://prod.mnd.cz/documents/view/{OTHER_DOCUMENT_UUID}"
PDF_BYTES = b"%PDF-1.7\nFRAKON MND pinned fixture\n%%EOF\n"
PDF_SHA256 = hashlib.sha256(PDF_BYTES).hexdigest()
OTHER_PDF_SHA256 = hashlib.sha256(b"%PDF-1.7\nother fixture\n%%EOF\n").hexdigest()


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
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components.frakon_energy.tariff_download",
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

    sources = _load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    mnd = _load(
        "custom_components.frakon_energy.providers.mnd_tariffs",
        "custom_components/frakon_energy/providers/mnd_tariffs.py",
    )
    resolver = _load(
        "custom_components.frakon_energy.providers.mnd_confirmed_source_resolver",
        "custom_components/frakon_energy/providers/mnd_confirmed_source_resolver.py",
    )
    selection = _load(
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components/frakon_energy/tariff_candidate_selection.py",
    )
    download = _load(
        "custom_components.frakon_energy.tariff_download",
        "custom_components/frakon_energy/tariff_download.py",
    )
    return sources, mnd, resolver, selection, download


def _context_fingerprint(sources, postcode: str = "41201") -> str:
    return sources.tariff_source_context_fingerprint(
        sources.TariffSourceResolutionContext(postcode=postcode)
    )


def _resolution(
    sources,
    resolver,
    *,
    postcode: str = "41201",
    product_name: str = "Proud - Ceník Říjen 28",
    distributor: str = "cez_distribuce",
    contract_kind: str = "fixed",
    source_url: str = OFFICIAL_URL,
    valid_from: date = date(2026, 6, 11),
    valid_to: date | None = date(2028, 10, 31),
    document_sha256: str = PDF_SHA256,
    confirmed_at: datetime = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc),
):
    return resolver.ConfirmedMndSourceResolution(
        source_context_fingerprint=_context_fingerprint(sources, postcode),
        product_name=product_name,
        distributor=distributor,
        contract_kind=contract_kind,
        source_url=source_url,
        valid_from=valid_from,
        valid_to=valid_to,
        document_date=valid_from,
        document_sha256=document_sha256,
        confirmed_at=confirmed_at,
    )


def _query(
    sources,
    *,
    postcode: str = "41201",
    product_name: str = "Proud - Ceník Říjen 28",
    distributor: str = "cez_distribuce",
    contract_kind: str = "fixed",
    valid_on: date = date(2026, 8, 15),
):
    return sources.TariffSourceQuery(
        supplier="mnd",
        product_name=product_name,
        distributor=distributor,
        contract_kind=contract_kind,
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_on=valid_on,
        source_context=sources.TariffSourceResolutionContext(postcode=postcode),
    )


def _clock() -> datetime:
    return datetime(2026, 8, 15, 8, 30, tzinfo=timezone.utc)


def test_confirmed_resolution_roundtrip_stores_only_context_fingerprint_not_raw_postcode() -> None:
    sources, _mnd, resolver, _selection, _download = load_modules()
    resolution = _resolution(sources, resolver, postcode="412 01")

    payload = resolution.as_dict()
    restored = resolver.ConfirmedMndSourceResolution.from_dict(payload)

    assert restored == resolution
    assert payload["source_context_fingerprint"] == _context_fingerprint(sources, "41201")
    assert "postcode" not in payload
    assert "41201" not in repr(payload)
    assert "412 01" not in repr(payload)
    assert payload["document_sha256"] == PDF_SHA256
    assert payload["source_url"] == OFFICIAL_URL


def test_resolution_fingerprint_excludes_confirmation_time_and_append_is_idempotent() -> None:
    sources, _mnd, resolver, _selection, _download = load_modules()
    first = _resolution(sources, resolver)
    repeated = _resolution(
        sources,
        resolver,
        confirmed_at=datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc),
    )

    assert resolver.confirmed_mnd_source_resolution_fingerprint(first) == (
        resolver.confirmed_mnd_source_resolution_fingerprint(repeated)
    )

    once = resolver.append_confirmed_mnd_source_resolution({}, first)
    twice = resolver.append_confirmed_mnd_source_resolution(once, repeated)

    assert twice == once
    stored = resolver.confirmed_mnd_source_resolutions_from_options(twice)
    assert stored == (first,)
    assert "41201" not in repr(twice)


def test_confirmed_resolution_requires_verified_product_public_validity_official_url_and_sha() -> None:
    sources, _mnd, resolver, _selection, _download = load_modules()

    with pytest.raises(ValueError, match="verified product"):
        _resolution(sources, resolver, product_name="Unknown MND Product")
    with pytest.raises(ValueError, match="public product evidence"):
        _resolution(sources, resolver, valid_to=date(2028, 12, 31))
    with pytest.raises(ValueError, match="official mnd.cz host"):
        _resolution(
            sources,
            resolver,
            source_url=f"https://example.com/documents/view/{DOCUMENT_UUID}",
        )
    with pytest.raises(ValueError, match="document_sha256"):
        _resolution(sources, resolver, document_sha256="not-a-sha")


def test_options_parser_fails_closed_on_corrupt_or_unknown_schema() -> None:
    _sources, _mnd, resolver, _selection, _download = load_modules()

    with pytest.raises(ValueError, match="must be a list"):
        resolver.confirmed_mnd_source_resolutions_from_options(
            {resolver.MND_CONFIRMED_SOURCE_RESOLUTIONS_OPTION: {}}
        )
    with pytest.raises(ValueError, match="unsupported confirmed MND source resolution schema"):
        resolver.confirmed_mnd_source_resolutions_from_options(
            {
                resolver.MND_CONFIRMED_SOURCE_RESOLUTIONS_OPTION: [
                    {"schema_version": 999}
                ]
            }
        )


def test_resolver_requires_exact_context_product_distributor_kind_and_day() -> None:
    sources, mnd, resolver, _selection, _download = load_modules()
    resolution = _resolution(sources, resolver)
    exact = resolver.MndConfirmedSourceResolver((resolution,), clock=_clock)
    product = mnd.MND_CURRENT_ELECTRICITY_PRODUCTS[0]

    resolved = __import__("asyncio").run(
        exact.async_resolve(_query(sources), product)
    )
    assert resolved is not None
    assert resolved.product_name == product.product_name
    assert resolved.distributor == "cez_distribuce"
    assert resolved.source_url == OFFICIAL_URL
    assert resolved.sha256 == PDF_SHA256
    assert resolved.discovered_at == _clock()

    assert __import__("asyncio").run(
        exact.async_resolve(_query(sources, postcode="11000"), product)
    ) is None
    assert __import__("asyncio").run(
        exact.async_resolve(_query(sources, distributor="eg_d"), product)
    ) is None
    assert __import__("asyncio").run(
        exact.async_resolve(_query(sources, valid_on=date(2029, 1, 1)), product)
    ) is None

    other_product = mnd.MND_CURRENT_ELECTRICITY_PRODUCTS[1]
    assert __import__("asyncio").run(
        exact.async_resolve(
            _query(
                sources,
                product_name=other_product.product_name,
                valid_on=date(2027, 1, 1),
            ),
            other_product,
        )
    ) is None


def test_overlapping_confirmed_resolutions_fail_closed_instead_of_picking_latest() -> None:
    sources, mnd, resolver, _selection, _download = load_modules()
    first = _resolution(sources, resolver)
    second = _resolution(
        sources,
        resolver,
        source_url=OTHER_OFFICIAL_URL,
        document_sha256=OTHER_PDF_SHA256,
        confirmed_at=datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc),
    )
    exact = resolver.MndConfirmedSourceResolver((first, second), clock=_clock)

    with pytest.raises(ValueError, match="ambiguous confirmed MND source resolution"):
        __import__("asyncio").run(
            exact.async_resolve(_query(sources), mnd.MND_CURRENT_ELECTRICITY_PRODUCTS[0])
        )


def test_adapter_candidate_carries_pinned_sha_and_download_rejects_changed_bytes() -> None:
    sources, mnd, resolver, selection, download = load_modules()
    confirmed = _resolution(sources, resolver)
    exact = resolver.MndConfirmedSourceResolver((confirmed,), clock=_clock)
    adapter = mnd.MndTariffCatalogAdapter(resolver=exact)
    query = _query(sources)

    candidates = __import__("asyncio").run(adapter.async_discover(query))
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.document.sha256 == PDF_SHA256
    assert "resolver pinned exact MND document SHA-256" in candidate.match_reasons
    assert "41201" not in repr(candidate)

    selected = selection.tariff_candidate_selection_fingerprint(candidate)
    validated = download.validate_selected_tariff_download(
        candidate=candidate,
        selected_fingerprint=selected,
        status_code=200,
        final_url=OFFICIAL_URL,
        content_type="application/pdf",
        content=PDF_BYTES,
        validated_at=_clock(),
    )
    assert validated.document.sha256 == PDF_SHA256

    with pytest.raises(ValueError, match="checksum does not match expected checksum"):
        download.validate_selected_tariff_download(
            candidate=candidate,
            selected_fingerprint=selected,
            status_code=200,
            final_url=OFFICIAL_URL,
            content_type="application/pdf",
            content=b"%PDF-1.7\nchanged document\n%%EOF\n",
            validated_at=_clock(),
        )


def test_resolver_factory_from_options_preserves_immutable_confirmed_source() -> None:
    sources, mnd, resolver, _selection, _download = load_modules()
    resolution = _resolution(sources, resolver)
    options = resolver.append_confirmed_mnd_source_resolution({}, resolution)
    built = resolver.mnd_confirmed_source_resolver_from_options(options, clock=_clock)

    result = __import__("asyncio").run(
        built.async_resolve(_query(sources), mnd.MND_CURRENT_ELECTRICITY_PRODUCTS[0])
    )
    assert result is not None
    assert result.source_url == OFFICIAL_URL
    assert result.sha256 == PDF_SHA256
