import asyncio
from datetime import date, datetime, timezone
import importlib.util
from pathlib import Path
import sys


def load_tariff_sources():
    path = Path("custom_components/frakon_energy/tariff_sources.py")
    spec = importlib.util.spec_from_file_location("frakon_energy_tariff_sources", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _query(module):
    return module.TariffSourceQuery(
        supplier="CEZ",
        product_name="Elektřina na 3 roky",
        distributor="cez_distribuce",
        contract_kind="fixed",
        distribution_tariff="d25D",
        breaker_code="3x25A",
        valid_on=date(2026, 8, 14),
    )


def _candidate(module, url: str):
    return module.TariffDocumentCandidate(
        document=module.OfficialTariffDocument(
            supplier="cez",
            source_url=url,
            discovered_at=datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc),
            document_date=date(2026, 7, 1),
            sha256="a" * 64,
            etag='"abc"',
            content_type="application/pdf",
        ),
        product_name="Elektřina na 3 roky",
        valid_from=date(2026, 7, 1),
        match_score=95,
        match_reasons=("exact product name", "matching D25d"),
    )


def test_query_normalizes_supplier_and_distribution_tariff() -> None:
    module = load_tariff_sources()
    query = _query(module)

    assert query.supplier == "cez"
    assert query.distribution_tariff == "D25d"
    assert query.breaker_code == "3x25A"


def test_registry_accepts_supplier_subdomain_and_rejects_lookalike_domain() -> None:
    module = load_tariff_sources()

    class Adapter:
        supplier = "cez"
        official_domains = ("cez.cz",)

        def __init__(self, url: str) -> None:
            self.url = url

        async def async_discover(self, query):
            return (_candidate(module, self.url),)

    registry = module.TariffAdapterRegistry()
    registry.register(Adapter("https://www.cez.cz/ceniky/elektrina.pdf"))
    candidates = asyncio.run(registry.async_discover_verified(_query(module)))
    assert len(candidates) == 1
    assert candidates[0].document.source_url.startswith("https://www.cez.cz/")

    hostile = module.TariffAdapterRegistry()
    hostile.register(Adapter("https://cez.cz.evil.example/cenik.pdf"))
    try:
        asyncio.run(hostile.async_discover_verified(_query(module)))
    except ValueError as err:
        assert "outside official domains" in str(err)
    else:
        raise AssertionError("Look-alike supplier domain must be rejected")


def test_registry_rejects_duplicate_supplier_adapter() -> None:
    module = load_tariff_sources()

    class Adapter:
        supplier = "eon"
        official_domains = ("eon.cz",)

        async def async_discover(self, query):
            return ()

    registry = module.TariffAdapterRegistry()
    registry.register(Adapter())
    try:
        registry.register(Adapter())
    except ValueError as err:
        assert "already registered" in str(err)
    else:
        raise AssertionError("Duplicate supplier adapter must be rejected")


def test_document_requires_safe_https_metadata() -> None:
    module = load_tariff_sources()
    now = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)

    for url in (
        "http://www.cez.cz/cenik.pdf",
        "https://user:password@www.cez.cz/cenik.pdf",
    ):
        try:
            module.OfficialTariffDocument(
                supplier="cez",
                source_url=url,
                discovered_at=now,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Unsafe supplier document URL must be rejected")

    try:
        module.OfficialTariffDocument(
            supplier="cez",
            source_url="https://www.cez.cz/cenik.pdf",
            discovered_at=now,
            sha256="xyz",
        )
    except ValueError as err:
        assert "sha256" in str(err)
    else:
        raise AssertionError("Malformed content checksum must be rejected")
