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


FIXED_NOW = datetime(2026, 8, 14, 16, 30, tzinfo=timezone.utc)


def load_module(*, fetch_mode="success", parser_mode="success"):
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
        "custom_components.frakon_energy.tariff_discovery_ws_api",
        "custom_components.frakon_energy.tariff_http_ha",
        "custom_components.frakon_energy.tariff_parser_preview",
        "custom_components.frakon_energy.tariff_pdf_text",
        "custom_components.frakon_energy.tariff_parse_preview_ws_api",
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
    discovery = _load(
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

    discovery_ws = types.ModuleType(
        "custom_components.frakon_energy.tariff_discovery_ws_api"
    )
    discovery_ws._registry_for_hass = (
        lambda hass: hass.data["frakon_energy"]["tariff_adapter_registry"]
    )
    sys.modules[discovery_ws.__name__] = discovery_ws

    fetch_calls = []
    content = b"%PDF-1.7\nvalidated parser preview fixture\n%%EOF"

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
                last_modified="Fri, 14 Aug 2026 14:00:00 GMT",
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
        if parser_mode == "extract_error":
            raise ValueError("PDF extraction failed")
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

    class SupplierTariffParsePreview:
        parsing_performed = True
        persistence_performed = False
        activation_performed = False

        def as_dict(self):
            return {
                "supplier": "cez",
                "product_name": "Basic",
                "high_rate_czk_per_kwh": "3.96",
                "low_rate_czk_per_kwh": "3.70",
                "supplier_standing_czk_month": "130.68",
                "document_sha256": hashlib.sha256(content).hexdigest(),
                "page_count": 2,
                "extraction_confidence": 100,
                "parsing_performed": True,
                "persistence_performed": False,
                "activation_performed": False,
            }

    def parse_supplier_tariff_preview(validated, extracted, contract):
        parser_calls.append((validated, extracted, contract))
        if parser_mode == "unsupported":
            raise LookupError("supplier parser preview is not implemented: eon")
        if parser_mode == "parse_error":
            raise ValueError("parsed product mismatch")
        return SupplierTariffParsePreview()

    parser_preview.SupplierTariffParsePreview = SupplierTariffParsePreview
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
    def require_admin(func):
        async def wrapped(hass, connection, msg):
            legacy = getattr(connection, "require_admin", None)
            if legacy is not None:
                legacy()
            return await func(hass, connection, msg)
        return wrapped

    websocket_api.require_admin = require_admin
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
        "custom_components.frakon_energy.tariff_parse_preview_ws_api",
        "custom_components/frakon_energy/tariff_parse_preview_ws_api.py",
    )
    return (
        ws,
        contracts,
        sources,
        selection,
        discovery,
        registered_commands,
        fetch_calls,
        extraction_calls,
        parser_calls,
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
    return types.SimpleNamespace(entry_id="entry-1", domain=domain)


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
        self.candidate = sources.TariffDocumentCandidate(
            document=sources.OfficialTariffDocument(
                supplier="cez",
                source_url="https://www.cez.cz/file/verified.pdf",
                discovered_at=datetime(2026, 8, 14, 16, 15, tzinfo=timezone.utc),
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

    async def async_discover(self, _query):
        return (self.candidate,)


def _registry(sources, adapter):
    registry = sources.TariffAdapterRegistry()
    registry.register(adapter)
    return registry


def _message(ws, contracts, fingerprint):
    return {
        "id": 31,
        "type": ws.COMMAND_TARIFF_PARSE_PREVIEW,
        "entry_id": "entry-1",
        "contract": _contract_dict(contracts),
        "day": "2026-08-14",
        "candidate_fingerprint": fingerprint,
    }


def test_success_runs_download_then_executor_parse_and_returns_no_raw_bytes_or_text() -> None:
    (
        ws,
        contracts,
        sources,
        selection,
        _discovery,
        registered,
        fetch_calls,
        extraction_calls,
        parser_calls,
        content,
    ) = load_module()
    adapter = Adapter(sources)
    hass = Hass(_entry(), _registry(sources, adapter))
    connection = Connection()
    fingerprint = selection.tariff_candidate_selection_fingerprint(adapter.candidate)

    ws.async_register_tariff_parse_preview_websocket(hass)
    asyncio.run(registered[0](hass, connection, _message(ws, contracts, fingerprint)))

    assert connection.admin_calls == 1
    assert connection.errors == []
    assert len(fetch_calls) == 1
    assert len(hass.executor_calls) == 1
    assert hass.executor_calls[0][0].__name__ == "_extract_and_parse"
    assert len(extraction_calls) == 1
    assert len(parser_calls) == 1

    payload = connection.results[0][1]
    assert payload["candidate_fingerprint"] == fingerprint
    assert payload["document_sha256"] == hashlib.sha256(content).hexdigest()
    assert payload["content_bytes"] == len(content)
    assert payload["download_performed"] is True
    assert payload["parsing_performed"] is True
    assert payload["persistence_performed"] is False
    assert payload["activation_performed"] is False
    assert payload["preview"]["high_rate_czk_per_kwh"] == "3.96"
    assert "content" not in payload
    assert "text" not in payload
    assert "text" not in payload["preview"]


def test_unknown_candidate_fingerprint_stops_before_network_or_executor() -> None:
    ws, contracts, sources, _selection, _discovery, registered, fetch_calls, extraction_calls, parser_calls, _content = load_module()
    adapter = Adapter(sources)
    hass = Hass(_entry(), _registry(sources, adapter))
    connection = Connection()
    ws.async_register_tariff_parse_preview_websocket(hass)

    asyncio.run(registered[0](hass, connection, _message(ws, contracts, "0" * 64)))

    assert connection.errors[0][1] == "candidate_not_found"
    assert fetch_calls == []
    assert hass.executor_calls == []
    assert extraction_calls == []
    assert parser_calls == []


def test_not_modified_without_cached_pdf_fails_before_parser_executor() -> None:
    ws, contracts, sources, selection, _discovery, registered, fetch_calls, extraction_calls, parser_calls, _content = load_module(fetch_mode="not_modified")
    adapter = Adapter(sources, etag='"etag-0"')
    hass = Hass(_entry(), _registry(sources, adapter))
    connection = Connection()
    fingerprint = selection.tariff_candidate_selection_fingerprint(adapter.candidate)
    ws.async_register_tariff_parse_preview_websocket(hass)

    asyncio.run(registered[0](hass, connection, _message(ws, contracts, fingerprint)))

    assert len(fetch_calls) == 1
    assert fetch_calls[0][2].headers_dict()["If-None-Match"] == '"etag-0"'
    assert connection.errors[0][1] == "not_modified_without_cached_document"
    assert hass.executor_calls == []
    assert extraction_calls == []
    assert parser_calls == []


def test_download_failure_has_no_parser_side_effects() -> None:
    ws, contracts, sources, selection, _discovery, registered, fetch_calls, extraction_calls, parser_calls, _content = load_module(fetch_mode="error")
    adapter = Adapter(sources)
    hass = Hass(_entry(), _registry(sources, adapter))
    connection = Connection()
    fingerprint = selection.tariff_candidate_selection_fingerprint(adapter.candidate)
    ws.async_register_tariff_parse_preview_websocket(hass)

    asyncio.run(registered[0](hass, connection, _message(ws, contracts, fingerprint)))

    assert len(fetch_calls) == 1
    assert connection.errors == [(31, "download_failed", "network unavailable")]
    assert hass.executor_calls == []
    assert extraction_calls == []
    assert parser_calls == []


def test_unsupported_parser_and_parse_errors_are_explicit_and_read_only() -> None:
    for parser_mode, expected_code in (
        ("unsupported", "parser_not_supported"),
        ("extract_error", "parse_failed"),
        ("parse_error", "parse_failed"),
    ):
        ws, contracts, sources, selection, _discovery, registered, _fetch_calls, _extraction_calls, _parser_calls, _content = load_module(parser_mode=parser_mode)
        adapter = Adapter(sources)
        hass = Hass(_entry(), _registry(sources, adapter))
        connection = Connection()
        fingerprint = selection.tariff_candidate_selection_fingerprint(adapter.candidate)
        ws.async_register_tariff_parse_preview_websocket(hass)

        asyncio.run(registered[0](hass, connection, _message(ws, contracts, fingerprint)))

        assert connection.results == []
        assert connection.errors[0][1] == expected_code
        assert len(hass.executor_calls) == 1


def test_registration_is_idempotent_and_entry_isolation_precedes_network() -> None:
    ws, contracts, sources, selection, _discovery, registered, fetch_calls, _extraction_calls, _parser_calls, _content = load_module()
    adapter = Adapter(sources)
    hass = Hass(_entry(domain="other_domain"), _registry(sources, adapter))
    fingerprint = selection.tariff_candidate_selection_fingerprint(adapter.candidate)

    ws.async_register_tariff_parse_preview_websocket(hass)
    ws.async_register_tariff_parse_preview_websocket(hass)
    assert len(registered) == 1

    connection = Connection()
    asyncio.run(registered[0](hass, connection, _message(ws, contracts, fingerprint)))

    assert connection.errors[0][1] == "entry_not_found"
    assert fetch_calls == []
    assert hass.executor_calls == []
