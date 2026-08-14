import asyncio
from datetime import date, datetime, timezone
import hashlib
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


FIXED_NOW = datetime(2026, 8, 14, 17, 0, tzinfo=timezone.utc)


def load_module(*, regulated_mode="success", fetch_mode="success", preview_mode="success"):
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.const",
        "custom_components.frakon_energy.contracts",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components.frakon_energy.tariff_discovery",
        "custom_components.frakon_energy.tariff_download",
        "custom_components.frakon_energy.tariff_fetch",
        "custom_components.frakon_energy.regulated_catalog",
        "custom_components.frakon_energy.tariff_all_in_preview",
        "custom_components.frakon_energy.tariff_discovery_ws_api",
        "custom_components.frakon_energy.tariff_http_ha",
        "custom_components.frakon_energy.tariff_parser_preview",
        "custom_components.frakon_energy.tariff_pdf_text",
        "custom_components.frakon_energy.tariff_all_in_preview_ws_api",
        "homeassistant",
        "homeassistant.components",
        "homeassistant.components.websocket_api",
        "homeassistant.core",
        "homeassistant.util",
        "homeassistant.util.dt",
        "voluptuous",
    )
    for name in names:
        sys.modules.pop(name, None)

    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    const = types.ModuleType("custom_components.frakon_energy.const")
    const.DOMAIN = "frakon_energy"
    sys.modules[const.__name__] = const

    contracts = _load(
        "custom_components.frakon_energy.contracts",
        "custom_components/frakon_energy/contracts.py",
    )
    sources = _load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    selection = _load(
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components/frakon_energy/tariff_candidate_selection.py",
    )
    _load(
        "custom_components.frakon_energy.tariff_discovery",
        "custom_components/frakon_energy/tariff_discovery.py",
    )
    download_module = _load(
        "custom_components.frakon_energy.tariff_download",
        "custom_components/frakon_energy/tariff_download.py",
    )
    fetch_module = _load(
        "custom_components.frakon_energy.tariff_fetch",
        "custom_components/frakon_energy/tariff_fetch.py",
    )

    regulated_calls = []
    regulated_catalog = types.ModuleType(
        "custom_components.frakon_energy.regulated_catalog"
    )

    class ConfirmedRegulatedTariffVersion:
        def __init__(self):
            self.bundle = types.SimpleNamespace(confirmed=True)
            self.evidence = (types.SimpleNamespace(scope="regulated"),)
            self.fingerprint = "r" * 64

    def select_confirmed_regulated_tariff_for_day(options, **kwargs):
        regulated_calls.append((options, kwargs))
        if regulated_mode == "missing":
            raise LookupError("no confirmed regulated tariff matches distributor/tariff/breaker/day")
        if regulated_mode == "invalid":
            raise ValueError("ambiguous confirmed regulated tariff versions for requested day")
        return ConfirmedRegulatedTariffVersion()

    regulated_catalog.ConfirmedRegulatedTariffVersion = ConfirmedRegulatedTariffVersion
    regulated_catalog.select_confirmed_regulated_tariff_for_day = (
        select_confirmed_regulated_tariff_for_day
    )
    sys.modules[regulated_catalog.__name__] = regulated_catalog

    all_in_calls = []
    all_in_module = types.ModuleType(
        "custom_components.frakon_energy.tariff_all_in_preview"
    )

    class AllInTariffPreview:
        persistence_performed = False
        activation_performed = False

        def as_dict(self):
            return {
                "supplier": "cez",
                "product_name": "Basic",
                "distribution_tariff": "D25d",
                "breaker_code": "3x25A",
                "all_in_vt_czk_kwh": "5.325243",
                "all_in_nt_czk_kwh": "4.460243",
                "fixed_monthly_total_czk": "388.2527",
                "provenance": {"evidence": [{"scope": "supplier_commercial"}, {"scope": "regulated"}]},
                "all_in_ready": True,
                "persistence_performed": False,
                "activation_performed": False,
            }

    def build_all_in_tariff_preview(*, download, parsed, contract, regulated, regulated_evidence):
        all_in_calls.append((download, parsed, contract, regulated, regulated_evidence))
        if preview_mode == "assembly_error":
            raise ValueError("regulated tariff breaker does not match customer breaker")
        return AllInTariffPreview()

    all_in_module.AllInTariffPreview = AllInTariffPreview
    all_in_module.build_all_in_tariff_preview = build_all_in_tariff_preview
    sys.modules[all_in_module.__name__] = all_in_module

    discovery_ws = types.ModuleType(
        "custom_components.frakon_energy.tariff_discovery_ws_api"
    )
    discovery_ws._registry_for_hass = (
        lambda hass: hass.data["frakon_energy"]["tariff_adapter_registry"]
    )
    sys.modules[discovery_ws.__name__] = discovery_ws

    fetch_calls = []
    content = b"%PDF-1.7\nvalidated all-in preview fixture\n%%EOF"
    http_ha = types.ModuleType("custom_components.frakon_energy.tariff_http_ha")

    async def async_fetch_selected_tariff_document_ha(
        hass, *, candidate, request, checked_at
    ):
        fetch_calls.append((hass, candidate, request, checked_at))
        if fetch_mode == "error":
            raise RuntimeError("network unavailable")
        if fetch_mode == "not_modified":
            return fetch_module.TariffNotModified(
                selected_fingerprint=request.selected_fingerprint,
                source_url=request.source_url,
                checked_at=checked_at,
                etag='"etag-1"',
                last_modified="Fri, 14 Aug 2026 15:00:00 GMT",
            )
        document = sources.OfficialTariffDocument(
            supplier=candidate.document.supplier,
            source_url=candidate.document.source_url,
            discovered_at=candidate.document.discovered_at,
            document_date=candidate.document.document_date,
            sha256=hashlib.sha256(content).hexdigest(),
            content_type="application/pdf",
        )
        return download_module.ValidatedTariffDownload(
            selected_fingerprint=request.selected_fingerprint,
            candidate=candidate,
            document=document,
            content=content,
            validated_at=checked_at,
        )

    http_ha.async_fetch_selected_tariff_document_ha = (
        async_fetch_selected_tariff_document_ha
    )
    sys.modules[http_ha.__name__] = http_ha

    extraction_calls = []
    pdf_text = types.ModuleType("custom_components.frakon_energy.tariff_pdf_text")

    def extract_validated_tariff_pdf_text(validated):
        extraction_calls.append(validated)
        return types.SimpleNamespace(
            source_url=validated.document.source_url,
            document_sha256=validated.document.sha256,
            page_count=2,
            text="bounded extracted fixture text",
            extraction_method="pypdf_layout",
            parser_authorized=True,
            persistence_performed=False,
            activation_performed=False,
        )

    pdf_text.extract_validated_tariff_pdf_text = extract_validated_tariff_pdf_text
    sys.modules[pdf_text.__name__] = pdf_text

    parser_calls = []
    parser_preview = types.ModuleType(
        "custom_components.frakon_energy.tariff_parser_preview"
    )

    def parse_supplier_tariff_preview(validated, extracted, contract):
        parser_calls.append((validated, extracted, contract))
        if preview_mode == "parse_error":
            raise ValueError("parsed product mismatch")
        return types.SimpleNamespace(
            supplier="cez",
            product_name="Basic",
            persistence_performed=False,
            activation_performed=False,
        )

    parser_preview.parse_supplier_tariff_preview = parse_supplier_tariff_preview
    sys.modules[parser_preview.__name__] = parser_preview

    vol = types.ModuleType("voluptuous")
    vol.Required = lambda key: key
    sys.modules["voluptuous"] = vol

    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    sys.modules["homeassistant"] = homeassistant

    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    sys.modules["homeassistant.components"] = components

    websocket_api = types.ModuleType("homeassistant.components.websocket_api")
    registered_commands = []

    class ActiveConnection:
        pass

    def websocket_command(_schema):
        def decorator(func):
            return func
        return decorator

    websocket_api.ActiveConnection = ActiveConnection
    websocket_api.websocket_command = websocket_command
    websocket_api.async_response = lambda func: func
    websocket_api.async_register_command = (
        lambda _hass, command: registered_commands.append(command)
    )
    sys.modules["homeassistant.components.websocket_api"] = websocket_api
    components.websocket_api = websocket_api

    core = types.ModuleType("homeassistant.core")
    core.callback = lambda func: func

    class HomeAssistant:
        pass

    core.HomeAssistant = HomeAssistant
    sys.modules["homeassistant.core"] = core

    util = types.ModuleType("homeassistant.util")
    util.__path__ = []
    sys.modules["homeassistant.util"] = util
    dt = types.ModuleType("homeassistant.util.dt")
    dt.now = lambda: FIXED_NOW
    sys.modules["homeassistant.util.dt"] = dt
    util.dt = dt

    ws = _load(
        "custom_components.frakon_energy.tariff_all_in_preview_ws_api",
        "custom_components/frakon_energy/tariff_all_in_preview_ws_api.py",
    )
    return (
        ws,
        contracts,
        sources,
        selection,
        registered_commands,
        regulated_calls,
        fetch_calls,
        extraction_calls,
        parser_calls,
        all_in_calls,
        content,
    )


class ConfigEntries:
    def __init__(self, entry):
        self.entry = entry

    def async_get_entry(self, entry_id):
        if self.entry is not None and self.entry.entry_id == entry_id:
            return self.entry
        return None


class Hass:
    def __init__(self, entry, registry):
        self.data = {"frakon_energy": {"tariff_adapter_registry": registry}}
        self.config_entries = ConfigEntries(entry)
        self.executor_calls = []

    async def async_add_executor_job(self, target, *args):
        self.executor_calls.append((target, args))
        return target(*args)


class Connection:
    def __init__(self):
        self.admin_calls = 0
        self.results = []
        self.errors = []

    def require_admin(self):
        self.admin_calls += 1

    def send_result(self, message_id, payload):
        self.results.append((message_id, payload))

    def send_error(self, message_id, code, message):
        self.errors.append((message_id, code, message))


def _entry(domain="frakon_energy"):
    return types.SimpleNamespace(
        entry_id="entry-1",
        domain=domain,
        options={"confirmed_regulated_tariffs": [{"fixture": True}]},
    )


def _contract_dict(contracts, *, supplier=None, product_name="Basic"):
    supplier = supplier or contracts.Supplier.CEZ
    fixed = supplier is not contracts.Supplier.CEZ
    return contracts.ElectricityContract(
        supplier=supplier,
        distributor=(
            contracts.Distributor.CEZ_DISTRIBUCE
            if supplier is contracts.Supplier.CEZ
            else contracts.Distributor.EG_D
        ),
        product_name=product_name,
        contract_kind=(
            contracts.ContractKind.FIXED
            if fixed
            else contracts.ContractKind.INDEFINITE
        ),
        distribution_tariff="D25d",
        breaker=contracts.Breaker(phases=3, amperes=25),
        valid_from=date(2026, 1, 1) if not fixed else date(2026, 3, 30),
        valid_to=date(2026, 12, 31),
        fixation_end=date(2028, 3, 29) if fixed else None,
        customer_confirmed=False,
    ).as_dict()


class Adapter:
    supplier = "cez"
    official_domains = ("cez.cz",)

    def __init__(self, sources, *, etag=None):
        self.calls = []
        self.candidate = sources.TariffDocumentCandidate(
            document=sources.OfficialTariffDocument(
                supplier="cez",
                source_url="https://www.cez.cz/file/verified.pdf",
                discovered_at=datetime(2026, 8, 14, 16, 45, tzinfo=timezone.utc),
                document_date=date(2026, 1, 1),
                etag=etag,
                content_type="application/pdf",
            ),
            product_name="Basic",
            valid_from=date(2026, 1, 1),
            match_score=100,
            match_reasons=("exact verified test match",),
            price_scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
        )

    async def async_discover(self, query):
        self.calls.append(query)
        return (self.candidate,)


def _registry(sources, adapter):
    registry = sources.TariffAdapterRegistry()
    registry.register(adapter)
    return registry


def _message(ws, contracts, fingerprint, *, contract=None):
    return {
        "id": 41,
        "type": ws.COMMAND_TARIFF_ALL_IN_PREVIEW,
        "entry_id": "entry-1",
        "contract": contract or _contract_dict(contracts),
        "day": "2026-08-14",
        "candidate_fingerprint": fingerprint,
    }


def test_success_selects_regulator_before_network_and_returns_complete_preview_only() -> None:
    (
        ws,
        contracts,
        sources,
        selection,
        registered,
        regulated_calls,
        fetch_calls,
        extraction_calls,
        parser_calls,
        all_in_calls,
        content,
    ) = load_module()
    adapter = Adapter(sources)
    entry = _entry()
    hass = Hass(entry, _registry(sources, adapter))
    connection = Connection()
    fingerprint = selection.tariff_candidate_selection_fingerprint(adapter.candidate)

    ws.async_register_tariff_all_in_preview_websocket(hass)
    asyncio.run(registered[0](hass, connection, _message(ws, contracts, fingerprint)))

    assert connection.admin_calls == 1
    assert connection.errors == []
    assert len(regulated_calls) == 1
    assert regulated_calls[0] == (
        entry.options,
        {
            "distributor": "cez_distribuce",
            "distribution_tariff": "D25d",
            "breaker_code": "3x25A",
            "day": date(2026, 8, 14),
        },
    )
    assert len(adapter.calls) == 1
    assert len(fetch_calls) == 1
    assert len(hass.executor_calls) == 1
    assert hass.executor_calls[0][0].__name__ == "_extract_parse_and_assemble"
    assert len(extraction_calls) == 1
    assert len(parser_calls) == 1
    assert len(all_in_calls) == 1

    payload = connection.results[0][1]
    assert payload["candidate_fingerprint"] == fingerprint
    assert payload["regulated_version_fingerprint"] == "r" * 64
    assert payload["document_sha256"] == hashlib.sha256(content).hexdigest()
    assert payload["content_bytes"] == len(content)
    assert payload["download_performed"] is True
    assert payload["parsing_performed"] is True
    assert payload["all_in_preview_performed"] is True
    assert payload["persistence_performed"] is False
    assert payload["activation_performed"] is False
    assert payload["preview"]["all_in_vt_czk_kwh"] == "5.325243"
    assert payload["preview"]["all_in_nt_czk_kwh"] == "4.460243"
    assert payload["preview"]["fixed_monthly_total_czk"] == "388.2527"
    assert payload["preview"]["all_in_ready"] is True
    assert "content" not in payload
    assert "text" not in payload
    assert "text" not in payload["preview"]


def test_missing_or_invalid_regulator_fails_before_discovery_and_network() -> None:
    for regulated_mode, expected_code in (
        ("missing", "regulated_tariff_not_available"),
        ("invalid", "regulated_tariff_invalid"),
    ):
        (
            ws,
            contracts,
            sources,
            selection,
            registered,
            regulated_calls,
            fetch_calls,
            _extraction_calls,
            _parser_calls,
            _all_in_calls,
            _content,
        ) = load_module(regulated_mode=regulated_mode)
        adapter = Adapter(sources)
        hass = Hass(_entry(), _registry(sources, adapter))
        connection = Connection()
        fingerprint = selection.tariff_candidate_selection_fingerprint(adapter.candidate)
        ws.async_register_tariff_all_in_preview_websocket(hass)

        asyncio.run(registered[0](hass, connection, _message(ws, contracts, fingerprint)))

        assert len(regulated_calls) == 1
        assert connection.errors[0][1] == expected_code
        assert adapter.calls == []
        assert fetch_calls == []
        assert hass.executor_calls == []


def test_unsupported_supplier_is_rejected_before_regulator_lookup_or_network() -> None:
    (
        ws,
        contracts,
        sources,
        _selection,
        registered,
        regulated_calls,
        fetch_calls,
        _extraction_calls,
        _parser_calls,
        _all_in_calls,
        _content,
    ) = load_module()
    adapter = Adapter(sources)
    hass = Hass(_entry(), _registry(sources, adapter))
    connection = Connection()
    eon_contract = _contract_dict(
        contracts,
        supplier=contracts.Supplier.EON,
        product_name="Variant PRO na 2 roky",
    )
    ws.async_register_tariff_all_in_preview_websocket(hass)

    asyncio.run(
        registered[0](
            hass,
            connection,
            _message(ws, contracts, "0" * 64, contract=eon_contract),
        )
    )

    assert connection.errors[0][1] == "parser_not_supported"
    assert regulated_calls == []
    assert adapter.calls == []
    assert fetch_calls == []
    assert hass.executor_calls == []


def test_unknown_candidate_stops_after_regulator_and_discovery_but_before_network() -> None:
    ws, contracts, sources, _selection, registered, regulated_calls, fetch_calls, _extraction_calls, _parser_calls, _all_in_calls, _content = load_module()
    adapter = Adapter(sources)
    hass = Hass(_entry(), _registry(sources, adapter))
    connection = Connection()
    ws.async_register_tariff_all_in_preview_websocket(hass)

    asyncio.run(registered[0](hass, connection, _message(ws, contracts, "0" * 64)))

    assert len(regulated_calls) == 1
    assert len(adapter.calls) == 1
    assert connection.errors[0][1] == "candidate_not_found"
    assert fetch_calls == []
    assert hass.executor_calls == []


def test_not_modified_without_cached_pdf_never_enters_executor() -> None:
    ws, contracts, sources, selection, registered, _regulated_calls, fetch_calls, extraction_calls, parser_calls, all_in_calls, _content = load_module(fetch_mode="not_modified")
    adapter = Adapter(sources, etag='"etag-0"')
    hass = Hass(_entry(), _registry(sources, adapter))
    connection = Connection()
    fingerprint = selection.tariff_candidate_selection_fingerprint(adapter.candidate)
    ws.async_register_tariff_all_in_preview_websocket(hass)

    asyncio.run(registered[0](hass, connection, _message(ws, contracts, fingerprint)))

    assert len(fetch_calls) == 1
    assert fetch_calls[0][2].headers_dict()["If-None-Match"] == '"etag-0"'
    assert connection.errors[0][1] == "not_modified_without_cached_document"
    assert hass.executor_calls == []
    assert extraction_calls == []
    assert parser_calls == []
    assert all_in_calls == []


def test_download_and_all_in_failures_remain_read_only() -> None:
    for fetch_mode, preview_mode, expected_code in (
        ("error", "success", "download_failed"),
        ("success", "parse_error", "all_in_preview_failed"),
        ("success", "assembly_error", "all_in_preview_failed"),
    ):
        ws, contracts, sources, selection, registered, _regulated_calls, _fetch_calls, _extraction_calls, _parser_calls, _all_in_calls, _content = load_module(fetch_mode=fetch_mode, preview_mode=preview_mode)
        adapter = Adapter(sources)
        hass = Hass(_entry(), _registry(sources, adapter))
        connection = Connection()
        fingerprint = selection.tariff_candidate_selection_fingerprint(adapter.candidate)
        ws.async_register_tariff_all_in_preview_websocket(hass)

        asyncio.run(registered[0](hass, connection, _message(ws, contracts, fingerprint)))

        assert connection.results == []
        assert connection.errors[0][1] == expected_code


def test_registration_is_idempotent_and_entry_isolation_precedes_regulator_lookup() -> None:
    ws, contracts, sources, selection, registered, regulated_calls, fetch_calls, _extraction_calls, _parser_calls, _all_in_calls, _content = load_module()
    adapter = Adapter(sources)
    hass = Hass(_entry(domain="other_domain"), _registry(sources, adapter))
    connection = Connection()
    fingerprint = selection.tariff_candidate_selection_fingerprint(adapter.candidate)

    ws.async_register_tariff_all_in_preview_websocket(hass)
    ws.async_register_tariff_all_in_preview_websocket(hass)
    assert len(registered) == 1

    asyncio.run(registered[0](hass, connection, _message(ws, contracts, fingerprint)))

    assert connection.errors[0][1] == "entry_not_found"
    assert regulated_calls == []
    assert fetch_calls == []
    assert hass.executor_calls == []
