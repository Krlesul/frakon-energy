from datetime import date
from decimal import Decimal
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


def load_modules():
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.pricing",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components.frakon_energy.cz_regulated_sources",
        "custom_components.frakon_energy.cz_regulated_2026_catalog",
        "custom_components.frakon_energy.tariff_assembly",
    )
    for name in names:
        sys.modules.pop(name, None)
    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    pricing = _load("custom_components.frakon_energy.pricing", "custom_components/frakon_energy/pricing.py")
    sources = _load("custom_components.frakon_energy.tariff_sources", "custom_components/frakon_energy/tariff_sources.py")
    provenance = _load("custom_components.frakon_energy.tariff_provenance", "custom_components/frakon_energy/tariff_provenance.py")
    _load("custom_components.frakon_energy.regulated_pricing", "custom_components/frakon_energy/regulated_pricing.py")
    _load("custom_components.frakon_energy.cz_regulated_sources", "custom_components/frakon_energy/cz_regulated_sources.py")
    catalog = _load("custom_components.frakon_energy.cz_regulated_2026_catalog", "custom_components/frakon_energy/cz_regulated_2026_catalog.py")
    assembly = _load("custom_components.frakon_energy.tariff_assembly", "custom_components/frakon_energy/tariff_assembly.py")
    return pricing, sources, provenance, catalog, assembly


def test_live_cez_indefinite_d25d_3x25_august_2026_all_in_totals() -> None:
    pricing, sources, provenance, catalog, assembly = load_modules()
    day = date(2026, 8, 16)
    inputs = catalog.official_2026_regulated_inputs(
        distributor="cez_distribuce",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        day=day,
    )
    regulated = inputs.to_bundle(confirmed=True)

    supplier_evidence = provenance.PriceEvidence(
        scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
        source_name="ČEZ Prodej",
        document_name="x01_moo_ee_na_dobu_neurcitou.pdf",
        source_url="https://www.cez.cz/file/edee/2025/10/x01_moo_ee_na_dobu_neurcitou.pdf",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        checksum="89c1c0c8676c94657c9bde4ca648c3e335e18dc526f7b8005a338a5222bb6d92",
    )
    multi = provenance.MultiSourceTariffProvenance((supplier_evidence, *inputs.regulated_evidence()))

    commodity = pricing.VariablePriceComponent(
        kind=pricing.PriceComponentKind.COMMODITY,
        name="ČEZ – obchodní cena elektřiny",
        high_rate_czk_per_kwh=Decimal("3.96"),
        low_rate_czk_per_kwh=Decimal("3.70"),
        includes_vat=True,
    )
    supplier_fixed = pricing.FixedPriceComponent(
        kind=pricing.PriceComponentKind.SUPPLIER_FIXED,
        name="ČEZ – stálá platba dodavatele",
        monthly_czk=Decimal("146.41"),
        includes_vat=True,
    )

    result = assembly.assemble_all_in_tariff(
        supplier="cez",
        product_name="Elektřina na dobu neurčitou",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        commercial_valid_from=date(2026, 1, 1),
        commercial_valid_to=date(2026, 12, 31),
        commodity=commodity,
        supplier_fixed=supplier_fixed,
        regulated=regulated,
        provenance=multi,
    )

    assert result.valid_from == date(2026, 6, 1)
    assert result.valid_to == date(2026, 12, 31)
    assert result.all_in_vt_czk_kwh == Decimal("6.9184379")
    assert result.all_in_nt_czk_kwh == Decimal("4.0739384")
    assert result.fixed_monthly_total_czk == Decimal("487.4727")
    assert result.all_in_ready is True
