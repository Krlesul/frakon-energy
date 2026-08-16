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


FIXED_NOW = datetime(2026, 8, 14, 15, 15, tzinfo=timezone.utc)


def load_module(*, fetch_error=None, not_modified=False):
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
        "custom_components.frakon_energy.tariff_download_preview_ws_api",
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
    download = _load(
        "custom_components.frakon_energy.tariff_download",
        "custom_components/frakon_energy/tariff_download.py",
    )
    fetch = _load(
        "custom_components.frakon_energy.tariff_fetch",
        "custom_components/frakon_energy/tariff_fetch.py",
    )

    discovery_ws = types.ModuleType(
        "custom_components.frakon_energy.tariff_discovery_ws_api"
    )

    def _registry_for_hass(hass):
        return hass.data["frakon_energy"]["tariff_adapter_registry"]

    discovery_ws._registry_for_hass = _registry_for_hass
    sys.modules[discovery_ws.__name__] = discovery_ws

    http_ha = types.ModuleType("custom_components.frakon_energy.tariff_http_ha")
    fetch_calls = []

    async def async_fetch_selected_tariff_document_ha(
        hass, *, candidate, request, checked_at
    ):
        fetch_calls.append((hass, candidate, request, checked_at))
        if fetch_error is not None:
            raise fetch_error
        if not_modified:
            return fetch.TariffNotModified(
                selected_fingerprint=request.selected_fingerprint,
                source_url=request.source_url,
                checked_at=checked_at,
                etag='"etag-2"',
                last_modified="Fri, 14 Aug 2026 12:00:00 GMT",
            )
        content = b"%PDF-1.7\nvalidated tariff preview\n%%EOF"
        document = sources.OfficialTariffDocument(
            supplier=candidate.document.supplier,
            source_url=candidate.document.source_url,
            discovered_at=candidate.document.discovered_at,
            document_date=candidate.document.document_date,
            sha256=hashlib.sha256(content).hexdigest(),
            etag='"etag-1"',
            last_modified="Fri, 14 Aug 2026 11:00:00 GMT",
            content_type="application/pdf",
        )
        return download.ValidatedTariffDownload(
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

    def async_response(func):
        return func

    def require_admin(func):
        async def wrapped(hass, connection, msg):
            legacy = getattr(connection, "require_admin", None)
            if legacy is not None:
                legacy()
            return await func(hass, connection, msg)
        return wrapped

    def async_register_command(_hass, command):
        registered_commands.append(command)

    websocket_api.ActiveConnection = ActiveConnection
    websocket_api.websocket_command = websocket_command
    websocket_api.async_response = async_response
    websocket_api.require_admin = require_admin
    websocket_api.async_register_command = async_register_command
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
        "custom_components.frakon_energy.tariff_download_preview_ws_api",
        "custom_components/frakon_energy/tariff_download_preview_ws_api.py",
    )
    return (
        ws,
        contracts,
        sources,
        selection,
        fetch,
        registered_commands,
        fetch_calls,
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


def _contract_dict(contracts):
    return contracts.ElectricityContract(
        supplier=contracts.Supplier.CEZ,
        distributor=contracts.Distributor.CEZ_DISTRIBUCE,
        product_name="Basic",
        contract_kind=contracts.ContractKind.INDEFINITE,
        distribution_tariff="D25d",
        breaker=contracts.Breaker(phases=3, amperes=25),
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
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
                discovered_at=datetime(2026, 8, 14, 14, 45, tzinfo=timezone.utc),
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


def test_exact_candidate_download_returns_only_validated_metadata() -> None:
    ws, contracts, sources, selection, _fetch, registered, fetch_calls = load_module()
    adapter = Adapter(sources)
    hass = Hass(_entry(), _registry(sources, adapter))
    connection = Connection()
    fingerprint = selection.tariff_candidate_selection_fingerprint(adapter.candidate)

    ws.async_register_tariff_download_preview_websocket(hass)
    asyncio.run(
        registered[0](
            hass,
            connection,
            {
                "id": 21,
                "type": ws.COMMAND_TARIFF_DOWNLOAD_PREVIEW,
                "entry_id": "entry-1",
                "contract": _contract_dict(contracts),
                "day": "2026-08-14",
                "candidate_fingerprint": fingerprint,
            },
        )
    )

    assert connection.admin_calls == 1
    assert connection.errors == []
    assert len(connection.results) == 1
    payload = connection.results[0][1]
    assert payload["candidate_fingerprint"] == fingerprint
    assert payload["source_url"] == "https://www.cez.cz/file/verified.pdf"
    assert len(payload["document_sha256"]) == 64
    assert payload["checked_at"] == FIXED_NOW.isoformat()
    assert payload["content_bytes"] > 0
    assert payload["body_downloaded"] is True
    assert payload["download_performed"] is True
    assert payload["parser_authorized"] is True
    assert payload["parsing_performed"] is False
    assert payload["persistence_performed"] is False
    assert payload["activation_performed"] is False
    assert "content" not in payload

    assert len(fetch_calls) == 1
    _hass, candidate, request, checked_at = fetch_calls[0]
    assert candidate is adapter.candidate
    assert checked_at == FIXED_NOW
    assert request.selected_fingerprint == fingerprint
    assert request.source_url == adapter.candidate.document.source_url
    assert request.allow_redirects is False
    assert request.max_bytes == 20 * 1024 * 1024
    assert request.headers_dict() == {"Accept": "application/pdf"}


def test_unknown_or_malformed_fingerprint_never_reaches_network() -> None:
    ws, contracts, sources, _selection, _fetch, registered, fetch_calls = load_module()
    adapter = Adapter(sources)
    hass = Hass(_entry(), _registry(sources, adapter))
    ws.async_register_tariff_download_preview_websocket(hass)

    unknown = Connection()
    asyncio.run(
        registered[0](
            hass,
            unknown,
            {
                "id": 22,
                "type": ws.COMMAND_TARIFF_DOWNLOAD_PREVIEW,
                "entry_id": "entry-1",
                "contract": _contract_dict(contracts),
                "day": "2026-08-14",
                "candidate_fingerprint": "0" * 64,
            },
        )
    )
    assert unknown.errors[0][1] == "candidate_not_found"

    malformed = Connection()
    asyncio.run(
        registered[0](
            hass,
            malformed,
            {
                "id": 23,
                "type": ws.COMMAND_TARIFF_DOWNLOAD_PREVIEW,
                "entry_id": "entry-1",
                "contract": _contract_dict(contracts),
                "day": "2026-08-14",
                "candidate_fingerprint": "not-a-fingerprint",
            },
        )
    )
    assert malformed.errors[0][1] == "invalid_candidate_selection"
    assert fetch_calls == []


def test_transport_failure_is_read_only_and_returns_explicit_error() -> None:
    ws, contracts, sources, selection, _fetch, registered, fetch_calls = load_module(
        fetch_error=RuntimeError("network unavailable")
    )
    adapter = Adapter(sources)
    hass = Hass(_entry(), _registry(sources, adapter))
    connection = Connection()
    fingerprint = selection.tariff_candidate_selection_fingerprint(adapter.candidate)
    ws.async_register_tariff_download_preview_websocket(hass)

    asyncio.run(
        registered[0](
            hass,
            connection,
            {
                "id": 24,
                "type": ws.COMMAND_TARIFF_DOWNLOAD_PREVIEW,
                "entry_id": "entry-1",
                "contract": _contract_dict(contracts),
                "day": "2026-08-14",
                "candidate_fingerprint": fingerprint,
            },
        )
    )

    assert len(fetch_calls) == 1
    assert connection.results == []
    assert connection.errors == [(24, "download_failed", "network unavailable")]


def test_conditional_not_modified_never_authorizes_parser_or_activation() -> None:
    ws, contracts, sources, selection, _fetch, registered, fetch_calls = load_module(
        not_modified=True
    )
    adapter = Adapter(sources, etag='"etag-0"')
    hass = Hass(_entry(), _registry(sources, adapter))
    connection = Connection()
    fingerprint = selection.tariff_candidate_selection_fingerprint(adapter.candidate)
    ws.async_register_tariff_download_preview_websocket(hass)

    asyncio.run(
        registered[0](
            hass,
            connection,
            {
                "id": 25,
                "type": ws.COMMAND_TARIFF_DOWNLOAD_PREVIEW,
                "entry_id": "entry-1",
                "contract": _contract_dict(contracts),
                "day": "2026-08-14",
                "candidate_fingerprint": fingerprint,
            },
        )
    )

    assert len(fetch_calls) == 1
    request = fetch_calls[0][2]
    assert request.headers_dict() == {
        "Accept": "application/pdf",
        "If-None-Match": '"etag-0"',
    }
    payload = connection.results[0][1]
    assert payload["body_downloaded"] is False
    assert payload["download_performed"] is False
    assert payload["parser_authorized"] is False
    assert payload["parsing_performed"] is False
    assert payload["persistence_performed"] is False
    assert payload["activation_performed"] is False


def test_registration_is_idempotent_and_wrong_entry_is_rejected() -> None:
    ws, contracts, sources, selection, _fetch, registered, fetch_calls = load_module()
    adapter = Adapter(sources)
    hass = Hass(_entry(domain="other_domain"), _registry(sources, adapter))
    fingerprint = selection.tariff_candidate_selection_fingerprint(adapter.candidate)

    ws.async_register_tariff_download_preview_websocket(hass)
    ws.async_register_tariff_download_preview_websocket(hass)
    assert len(registered) == 1

    connection = Connection()
    asyncio.run(
        registered[0](
            hass,
            connection,
            {
                "id": 26,
                "type": ws.COMMAND_TARIFF_DOWNLOAD_PREVIEW,
                "entry_id": "entry-1",
                "contract": _contract_dict(contracts),
                "day": "2026-08-14",
                "candidate_fingerprint": fingerprint,
            },
        )
    )

    assert connection.errors[0][1] == "entry_not_found"
    assert connection.results == []
    assert fetch_calls == []
