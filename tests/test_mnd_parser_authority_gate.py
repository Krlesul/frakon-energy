import asyncio
from datetime import date, datetime, timezone
import importlib.util
from pathlib import Path
import sys
import types


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_registry_stack():
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.providers",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.providers.cez_tariffs",
        "custom_components.frakon_energy.providers.eon_tariffs",
        "custom_components.frakon_energy.providers.pre_tariffs",
        "custom_components.frakon_energy.providers.mnd_tariffs",
        "custom_components.frakon_energy.providers.mnd_confirmed_source_resolver",
        "custom_components.frakon_energy.tariff_adapter_registry",
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
    _load(
        "custom_components.frakon_energy.providers.cez_tariffs",
        "custom_components/frakon_energy/providers/cez_tariffs.py",
    )
    _load(
        "custom_components.frakon_energy.providers.eon_tariffs",
        "custom_components/frakon_energy/providers/eon_tariffs.py",
    )
    _load(
        "custom_components.frakon_energy.providers.pre_tariffs",
        "custom_components/frakon_energy/providers/pre_tariffs.py",
    )
    _load(
        "custom_components.frakon_energy.providers.mnd_tariffs",
        "custom_components/frakon_energy/providers/mnd_tariffs.py",
    )
    confirmed = _load(
        "custom_components.frakon_energy.providers.mnd_confirmed_source_resolver",
        "custom_components/frakon_energy/providers/mnd_confirmed_source_resolver.py",
    )
    registry = _load(
        "custom_components.frakon_energy.tariff_adapter_registry",
        "custom_components/frakon_energy/tariff_adapter_registry.py",
    )
    return sources, confirmed, registry


def _load_parser_support():
    for name in (
        "custom_components.frakon_energy.contracts",
        "custom_components.frakon_energy.providers.cez_tariff_parser",
        "custom_components.frakon_energy.tariff_download",
        "custom_components.frakon_energy.tariff_pdf_text",
        "custom_components.frakon_energy.tariff_parser_preview",
    ):
        sys.modules.pop(name, None)

    contracts = _load(
        "custom_components.frakon_energy.contracts",
        "custom_components/frakon_energy/contracts.py",
    )

    cez_parser = types.ModuleType(
        "custom_components.frakon_energy.providers.cez_tariff_parser"
    )
    cez_parser.parse_cez_commercial_price_text = lambda *args, **kwargs: None
    sys.modules[cez_parser.__name__] = cez_parser

    download = types.ModuleType("custom_components.frakon_energy.tariff_download")

    class ValidatedTariffDownload:
        pass

    download.ValidatedTariffDownload = ValidatedTariffDownload
    sys.modules[download.__name__] = download

    pdf = types.ModuleType("custom_components.frakon_energy.tariff_pdf_text")

    class ExtractedTariffPdfText:
        pass

    pdf.ExtractedTariffPdfText = ExtractedTariffPdfText
    sys.modules[pdf.__name__] = pdf

    parser = _load(
        "custom_components.frakon_energy.tariff_parser_preview",
        "custom_components/frakon_energy/tariff_parser_preview.py",
    )
    return contracts, parser


def test_confirmed_sha_pinned_mnd_source_does_not_grant_price_parser_authority() -> None:
    sources, confirmed, registry_module = _load_registry_stack()
    postcode = "41201"
    source_context = sources.TariffSourceResolutionContext(postcode=postcode)
    resolution = confirmed.ConfirmedMndSourceResolution(
        source_context_fingerprint=sources.tariff_source_context_fingerprint(
            source_context
        ),
        product_name="Proud - Ceník Říjen 28",
        distributor="cez_distribuce",
        contract_kind="fixed",
        source_url=(
            "https://prod.mnd.cz/documents/view/"
            "12345678-1234-4234-8234-123456789abc"
        ),
        valid_from=date(2026, 6, 11),
        valid_to=date(2028, 10, 31),
        document_date=date(2026, 6, 11),
        document_sha256="a" * 64,
        confirmed_at=datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc),
    )
    options = {
        confirmed.MND_CONFIRMED_SOURCE_RESOLUTIONS_OPTION: [resolution.as_dict()]
    }
    registry = registry_module.build_entry_tariff_adapter_registry(
        options,
        clock=lambda: datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc),
    )
    query = sources.TariffSourceQuery(
        supplier="mnd",
        product_name="Proud - Ceník Říjen 28",
        distributor="cez_distribuce",
        contract_kind="fixed",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_on=date(2026, 8, 15),
        source_context=source_context,
    )

    candidates = asyncio.run(registry.async_discover_verified(query))

    assert len(candidates) == 1
    assert candidates[0].document.supplier == "mnd"
    assert candidates[0].document.sha256 == "a" * 64
    assert candidates[0].document.source_url.startswith(
        "https://prod.mnd.cz/documents/view/"
    )
    assert candidates[0].price_scope == sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL

    contracts, parser = _load_parser_support()
    assert parser.supplier_parser_supported(contracts.Supplier.CEZ) is True
    assert parser.supplier_parser_supported(contracts.Supplier.EON) is True
    assert parser.supplier_parser_supported(contracts.Supplier.PRE) is True
    assert parser.supplier_parser_supported(contracts.Supplier.MND) is False
    assert parser.supplier_parser_supported("mnd") is False
