import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types


def load_modules():
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.pricing",
        "custom_components.frakon_energy.contracts",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components.frakon_energy.tariff_download",
        "custom_components.frakon_energy.tariff_fetch",
        "custom_components.frakon_energy.tariff_http_transport",
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components.frakon_energy.cz_regulated_sources",
        "custom_components.frakon_energy.tariff_assembly",
        "custom_components.frakon_energy.all_in_catalog",
        "custom_components.frakon_energy.tariff_source_watch",
        "custom_components.frakon_energy.tariff_source_watch_store",
        "custom_components.frakon_energy.tariff_source_watch_fetch",
        "custom_components.frakon_energy.tariff_update_orchestrator",
    )
    for name in names:
        sys.modules.pop(name, None)

    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, Path(path))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    pricing = load(
        "custom_components.frakon_energy.pricing",
        "custom_components/frakon_energy/pricing.py",
    )
    contracts = load(
        "custom_components.frakon_energy.contracts",
        "custom_components/frakon_energy/contracts.py",
    )
    sources = load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    load(
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components/frakon_energy/tariff_candidate_selection.py",
    )
    load(
        "custom_components.frakon_energy.tariff_download",
        "custom_components/frakon_energy/tariff_download.py",
    )
    load(
        "custom_components.frakon_energy.tariff_fetch",
        "custom_components/frakon_energy/tariff_fetch.py",
    )
    load(
        "custom_components.frakon_energy.tariff_http_transport",
        "custom_components/frakon_energy/tariff_http_transport.py",
    )
    provenance = load(
        "custom_components.frakon_energy.tariff_provenance",
        "custom_components/frakon_energy/tariff_provenance.py",
    )
    load(
        "custom_components.frakon_energy.regulated_pricing",
        "custom_components/frakon_energy/regulated_pricing.py",
    )
    cz = load(
        "custom_components.frakon_energy.cz_regulated_sources",
        "custom_components/frakon_energy/cz_regulated_sources.py",
    )
    assembly = load(
        "custom_components.frakon_energy.tariff_assembly",
        "custom_components/frakon_energy/tariff_assembly.py",
    )
    catalog = load(
        "custom_components.frakon_energy.all_in_catalog",
        "custom_components/frakon_energy/all_in_catalog.py",
    )
    watch = load(
        "custom_components.frakon_energy.tariff_source_watch",
        "custom_components/frakon_energy/tariff_source_watch.py",
    )
    store = load(
        "custom_components.frakon_energy.tariff_source_watch_store",
        "custom_components/frakon_energy/tariff_source_watch_store.py",
    )
    load(
        "custom_components.frakon_energy.tariff_source_watch_fetch",
        "custom_components/frakon_energy/tariff_source_watch_fetch.py",
    )
    orchestrator = load(
        "custom_components.frakon_energy.tariff_update_orchestrator",
        "custom_components/frakon_energy/tariff_update_orchestrator.py",
    )
    return pricing, contracts, sources, provenance, cz, assembly, catalog, watch, store, orchestrator


def _regulated_inputs(cz):
    eru = cz.RegulatedPriceSource(
        authority=cz.RegulatedAuthority.ERU,
        document_id="Cenový výměr 14/2025",
        source_url="https://eru.gov.cz/energeticky-regulacni-vestnik-182025",
        valid_from=date(2026, 1, 1),
    )
    ote = cz.RegulatedPriceSource(
        authority=cz.RegulatedAuthority.OTE,
        document_id="OTE 2026",
        source_url="https://www.ote-cr.cz/cs/registrace-a-smlouvy/smluvni-vztahy-elektrina/ceny-za-sluzby-ote",
        valid_from=date(2026, 1, 1),
    )
    return cz.CzechRegulatedTariffInputs(
        distributor="cez_distribuce",
        distribution_tariff="D25d",
        breaker_code="3x25A",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        distribution_vt_czk_per_kwh=Decimal("1.000"),
        distribution_nt_czk_per_kwh=Decimal("0.500"),
        breaker_monthly_czk=Decimal("200"),
        system_services_czk_per_kwh=Decimal("0.100"),
        electricity_tax_czk_per_kwh=Decimal("0.02830"),
        sources=(eru, ote),
    )


def _assembly(modules, *, product="Basic", checksum="a" * 64, tariff="D25d", breaker="3x25A"):
    pricing, _, sources, provenance, cz, assembly, _, _, _, _ = modules
    inputs = _regulated_inputs(cz)
    regulated = inputs.to_bundle(confirmed=True)
    supplier_evidence = provenance.PriceEvidence(
        scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
        source_name="ČEZ Prodej",
        document_name=f"{product} 2026",
        source_url="https://www.cez.cz/file/edee/basic-2026.pdf",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        document_date=date(2025, 10, 1),
        checksum=checksum,
    )
    evidence = provenance.MultiSourceTariffProvenance(
        (supplier_evidence, *inputs.regulated_evidence())
    )
    commodity = pricing.VariablePriceComponent(
        pricing.PriceComponentKind.COMMODITY,
        f"ČEZ {product} commodity",
        Decimal("3.960"),
        Decimal("3.700"),
    )
    supplier_fixed = pricing.FixedPriceComponent(
        pricing.PriceComponentKind.SUPPLIER_FIXED,
        f"ČEZ {product} stálá platba",
        Decimal("130.68"),
    )
    result = assembly.assemble_all_in_tariff(
        supplier="ČEZ",
        product_name=product,
        distribution_tariff="D25d",
        breaker_code="3x25A",
        commercial_valid_from=date(2026, 1, 1),
        commercial_valid_to=date(2026, 12, 31),
        commodity=commodity,
        supplier_fixed=supplier_fixed,
        regulated=regulated,
        provenance=evidence,
    )
    if tariff != "D25d" or breaker != "3x25A":
        # Constructing an inconsistent all-in assembly directly should not bypass
        # its own regulated boundary, so mismatch tests vary the contract instead.
        raise AssertionError("Use contract overrides for tariff/breaker mismatch tests")
    return result


def _confirmed_options(
    modules,
    *,
    product="Basic",
    checksum="a" * 64,
    contract_product=None,
    contract_tariff="D25d",
    contract_breaker=(3, 25),
):
    _, contracts, _, _, _, _, catalog, _, _, _ = modules
    contract = contracts.ElectricityContract(
        supplier=contracts.Supplier.CEZ,
        distributor=contracts.Distributor.CEZ_DISTRIBUCE,
        product_name=contract_product or product,
        contract_kind=contracts.ContractKind.INDEFINITE,
        distribution_tariff=contract_tariff,
        breaker=contracts.Breaker(*contract_breaker),
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        customer_confirmed=False,
    )
    options = contracts.append_electricity_contract({}, contract)
    options = contracts.confirm_electricity_contract(
        options,
        contracts.contract_fingerprint(contract),
    )
    all_in = _assembly(modules, product=product, checksum=checksum)
    options = catalog.append_all_in_tariff(options, all_in)
    item = catalog.PersistedAllInTariff(assembly=all_in, confirmed=False)
    options = catalog.confirm_all_in_tariff(
        options,
        catalog.all_in_tariff_fingerprint(item),
    )
    return options


def _checked_at():
    return datetime(2026, 8, 14, 8, 30, tzinfo=timezone.utc)


def _pdf():
    return b"%PDF-1.7\nnew source version\n%%EOF\n"


class FakeContent:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def iter_chunked(self, _size):
        async def iterator():
            for chunk in self.chunks:
                yield chunk
        return iterator()


class FakeResponse:
    def __init__(self, *, status, url, headers, chunks):
        self.status = status
        self.url = url
        self.headers = headers
        self.content = FakeContent(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class ExplodingSession:
    def get(self, url, **kwargs):
        raise RuntimeError("supplier endpoint offline")


def test_prepare_active_watch_seeds_store_only_from_confirmed_contract_and_all_in() -> None:
    modules = load_modules()
    _, contracts, _, _, _, _, catalog, watch, store, orchestrator = modules
    options = _confirmed_options(modules)
    options["unrelated"] = {"keep": True}

    prepared = orchestrator.prepare_active_tariff_source_watch(
        options,
        day=date(2026, 8, 14),
    )

    assert prepared.rebind_performed is False
    assert prepared.record.watch.supplier == "cez"
    assert prepared.record.watch.product_name == "Basic"
    assert prepared.record.watch.active_sha256 == "a" * 64
    assert prepared.updated_options["unrelated"] == {"keep": True}
    assert prepared.contract_fingerprint == contracts.contract_fingerprint(
        contracts.confirmed_contract_from_options(options, date(2026, 8, 14))
    )
    assert prepared.all_in_fingerprint == catalog.all_in_tariff_fingerprint(
        catalog.confirmed_all_in_tariff_from_options(options, date(2026, 8, 14))
    )
    record = store.tariff_source_watch_record_from_options(
        prepared.updated_options,
        prepared.watch_fingerprint,
    )
    assert record == prepared.record
    assert prepared.watch_fingerprint == watch.tariff_source_watch_fingerprint(record.watch)


def test_prepare_reuses_durable_http_validators_for_same_active_hash() -> None:
    modules = load_modules()
    _, _, _, _, _, _, _, watch, store, orchestrator = modules
    options = _confirmed_options(modules)
    prepared = orchestrator.prepare_active_tariff_source_watch(
        options,
        day=date(2026, 8, 14),
    )
    not_modified = watch.tariff_source_not_modified(
        prepared.record.watch,
        checked_at=_checked_at(),
        etag='"v1"',
        last_modified="Fri, 14 Aug 2026 06:00:00 GMT",
    )
    options = store.record_tariff_source_check(
        prepared.updated_options,
        not_modified,
    )

    again = orchestrator.prepare_active_tariff_source_watch(
        options,
        day=date(2026, 8, 14),
    )
    assert again.rebind_performed is False
    assert again.record.watch.etag == '"v1"'
    assert again.record.watch.last_modified == "Fri, 14 Aug 2026 06:00:00 GMT"


def test_prepare_rejects_contract_all_in_product_tariff_and_breaker_mismatch() -> None:
    modules = load_modules()
    *_, orchestrator = modules
    mismatched = (
        _confirmed_options(modules, product="Basic", contract_product="eTarif"),
        _confirmed_options(modules, contract_tariff="D27d"),
        _confirmed_options(modules, contract_breaker=(3, 32)),
    )
    expected = ("product", "distribution tariff", "breaker")
    for options, marker in zip(mismatched, expected, strict=True):
        try:
            orchestrator.prepare_active_tariff_source_watch(
                options,
                day=date(2026, 8, 14),
            )
        except ValueError as err:
            assert marker in str(err)
        else:
            raise AssertionError("Confirmed contract/all-in mismatch must fail closed")


def test_confirmed_new_hash_rebinds_same_watch_and_clears_pending_only_via_confirmed_authority() -> None:
    modules = load_modules()
    _, _, sources, _, _, _, _, watch, store, orchestrator = modules
    options = _confirmed_options(modules, checksum="b" * 64)

    # Seed operational state from the previously active confirmed version. It has
    # the same watch target identity but old active hash A and pending observed B.
    old_watch = watch.TariffSourceWatch(
        supplier="cez",
        product_name="Basic",
        source_name="ČEZ Prodej",
        document_name="Basic 2026",
        source_url="https://www.cez.cz/file/edee/basic-2026.pdf",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        active_sha256="a" * 64,
        document_date=date(2025, 10, 1),
    )
    options = store.append_tariff_source_watch(options, old_watch)
    changed = watch.evaluate_tariff_source_download(
        old_watch,
        document=sources.OfficialTariffDocument(
            supplier="cez",
            source_url=old_watch.source_url,
            discovered_at=_checked_at(),
            sha256="b" * 64,
            etag='"v2"',
            last_modified="Fri, 14 Aug 2026 06:00:00 GMT",
        ),
        checked_at=_checked_at(),
    )
    options = store.record_tariff_source_check(options, changed)

    prepared = orchestrator.prepare_active_tariff_source_watch(
        options,
        day=date(2026, 8, 14),
    )

    assert prepared.rebind_performed is True
    assert prepared.record.watch.active_sha256 == "b" * 64
    assert prepared.record.watch.etag == '"v2"'
    assert prepared.record.pending_sha256 is None
    assert prepared.record.last_check is None


def test_async_active_check_persists_changed_hash_as_pending_without_activation() -> None:
    modules = load_modules()
    *_, store, orchestrator = modules
    options = _confirmed_options(modules, checksum="a" * 64)
    content = _pdf()
    response = FakeResponse(
        status=200,
        url="https://www.cez.cz/file/edee/basic-2026.pdf",
        headers={
            "Content-Type": "application/pdf",
            "Content-Length": str(len(content)),
            "ETag": '"v2"',
        },
        chunks=(content,),
    )

    run = asyncio.run(
        orchestrator.async_check_active_tariff_source(
            options,
            day=date(2026, 8, 14),
            session=FakeSession(response),
            checked_at=_checked_at(),
        )
    )

    assert run.check.status == "change_detected"
    assert run.check.active_sha256 == "a" * 64
    assert run.check.requires_confirmation is True
    assert run.parser_authorized is True
    assert run.activation_performed is False
    record = store.tariff_source_watch_record_from_options(
        run.updated_options,
        run.prepared.watch_fingerprint,
    )
    assert record.watch.active_sha256 == "a" * 64
    assert record.pending_sha256 == run.check.observed_sha256


def test_async_active_check_captures_operational_error_and_preserves_active_state() -> None:
    modules = load_modules()
    *_, store, orchestrator = modules
    options = _confirmed_options(modules)

    run = asyncio.run(
        orchestrator.async_check_active_tariff_source(
            options,
            day=date(2026, 8, 14),
            session=ExplodingSession(),
            checked_at=_checked_at(),
        )
    )

    assert run.error_captured is True
    assert run.outcome is None
    assert run.check.status == "error"
    assert "supplier endpoint offline" in run.check.error
    assert run.check.active_sha256 == "a" * 64
    assert run.activation_performed is False
    record = store.tariff_source_watch_record_from_options(
        run.updated_options,
        run.prepared.watch_fingerprint,
    )
    assert record.watch.active_sha256 == "a" * 64
    assert record.last_check.status == "error"
