import importlib.util
from pathlib import Path
import sys


def load_sources():
    path = Path("custom_components/frakon_energy/official_tariff_sources.py")
    spec = importlib.util.spec_from_file_location("frakon_energy_official_tariff_sources", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_supported_suppliers_use_verified_official_discovery_roots() -> None:
    sources = load_sources()

    assert sources.official_price_list_landing_url("cez") == "https://www.cez.cz/cs/podpora/ceniky"
    assert sources.official_price_list_landing_url("eon") == "https://www.eon.cz/domacnosti/zakaznicka-pece/ceniky/"
    assert sources.official_price_list_landing_url("pre") == "https://www.pre.cz/cs/domacnosti/elektrina/prehled-produktu/aktualni/"
    assert sources.official_price_list_landing_url("mnd") == "https://www.mnd.cz/Dokumenty-ke-stazeni"


def test_supplier_owned_https_subdomains_are_allowed() -> None:
    sources = load_sources()

    assert sources.is_official_supplier_source_url("cez", "https://www.cez.cz/cs/podpora/ceniky")
    assert sources.is_official_supplier_source_url("eon", "https://static.eon.cz/cenik.pdf")
    assert sources.is_official_supplier_source_url("pre", "https://cdn.pre.cz/ceniky/elektrina.pdf")
    assert sources.is_official_supplier_source_url("mnd", "https://prod.mnd.cz/elektrina-domacnosti")
    assert sources.is_official_supplier_source_url("mnd", "https://mnd.cz:443/cenik.pdf")


def test_spoofed_insecure_cross_supplier_and_credential_urls_are_rejected() -> None:
    sources = load_sources()

    rejected = (
        ("cez", "http://www.cez.cz/cs/podpora/ceniky"),
        ("cez", "https://cez.cz.evil.example/cenik.pdf"),
        ("cez", "https://evilcez.cz/cenik.pdf"),
        ("cez", "https://user:password@www.cez.cz/cenik.pdf"),
        ("cez", "https://www.cez.cz:8443/cenik.pdf"),
        ("cez", "https://www.eon.cz/domacnosti/zakaznicka-pece/ceniky/"),
        ("eon", "https://www.pre.cz/cenik.pdf"),
    )
    for supplier, url in rejected:
        assert sources.is_official_supplier_source_url(supplier, url) is False


def test_unsupported_supplier_has_no_automated_source_and_requirement_fails_closed() -> None:
    sources = load_sources()

    try:
        sources.official_price_list_landing_url("other")
    except LookupError:
        pass
    else:
        raise AssertionError("Unsupported supplier must not get an automated discovery root")

    assert sources.is_official_supplier_source_url("other", "https://example.com/cenik.pdf") is False

    try:
        sources.require_official_supplier_source_url("cez", "https://example.com/cenik.pdf")
    except ValueError as err:
        assert "official supplier trust boundary" in str(err)
    else:
        raise AssertionError("Non-official URL must be rejected before automated download")
