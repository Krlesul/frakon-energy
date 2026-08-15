import asyncio
from datetime import date, datetime, timezone
import hashlib
import importlib.util
from pathlib import Path
import sys
import types


FIXED_NOW = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)
PDF_BYTES = b"%PDF-1.7\nMND exact source proposal fixture\n%%EOF\n"
PDF_SHA256 = hashlib.sha256(PDF_BYTES).hexdigest()
DOCUMENT_UUID = "12345678-1234-4234-8234-123456789abc"
OFFICIAL_URL = f"https://prod.mnd.cz/documents/view/{DOCUMENT_UUID}"


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_module(*, download_mode: str = "success"):
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.providers",
        "custom_components.frakon_energy.const",
        "custom_components.frakon_energy.contracts",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_source_context",
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components.frakon_energy.tariff_download",
        "custom_components.frakon_energy.tariff_fetch",
        "custom_components.frakon_energy.tariff_http_ha",
        "custom_components.frakon_energy.providers.mnd_tariffs",
        "custom_components.frakon_energy.providers.mnd_confirmed_source_resolver",
        "custom_components.frakon_energy.providers.mnd_source_proposals",
        "custom_components.frakon_energy.providers.mnd_source_proposals_ws_api",
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
    for name in (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.providers",
    ):
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
    context = _load(
        "custom_components.frakon_energy.tariff_source_context",
        "custom_components/frakon_energy/tariff_source_context.py",
    )
    selection = _load(
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components/frakon_energy/tariff_candidate_selection.py",
    )
    download = _load(
        "custom_components.frakon_energy.tariff_download",
        "custom_components/frakon_energy/tariff_download.py",
    )
    fetch = _load(
        "custom_components.frakon_energy.tariff_fetch",
        "custom_components/frakon_energy/tariff_fetch.py",
    )
    mnd = _load(
        "custom_components.frakon_energy.providers.mnd_tariffs",
        "custom_components/frakon_energy/providers/mnd_tariffs.py",
    )
    resolver = _load(
        "custom_components.frakon_energy.providers.mnd_confirmed_source_resolver",
        "custom_components/frakon_energy/providers/mnd_confirmed_source_resolver.py",
    )
    proposals = _load(
        "custom_components.frakon_energy.providers.mnd_source_proposals",
        "custom_components/frakon_energy/providers/mnd_source_proposals.py",
    )

    fetch_calls = []
    http_ha = types.ModuleType("custom_components.frakon_energy.tariff_http_ha")

    async def async_fetch_selected_tariff_document_ha(
        hass,
        *,
        candidate,
        request,
        checked_at,
        timeout_seconds=20,
    ):
        fetch_calls.append((candidate, request, checked_at))
        if download_mode == "error":
            raise RuntimeError("network failed")
        if download_mode == "not_modified":
            return fetch.TariffNotModified(
                selected_fingerprint=request.selected_fingerprint,
                source_url=request.source_url,
                checked_at=checked_at,
                etag='"etag"',
                last_modified=None,
            )
        return download.validate_selected_tariff_download(
            candidate=candidate,
            selected_fingerprint=request.selected_fingerprint,
            status_code=200,
            final_url=request.source_url,
            content_type="application/pdf",
            content=PDF_BYTES,
            validated_at=checked_at,
        )

    http_ha.async_fetch_selected_tariff_document_ha = async_fetch_selected_tariff_document_ha
    sys.modules[http_ha.__name__] = http_ha

    schemas = []
    vol = types.ModuleType("voluptuous")
    vol.Required = lambda key: key
    vol.Optional = lambda key: key
    sys.modules["voluptuous"] = vol

    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    sys.modules["homeassistant"] = homeassistant
    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    sys.modules["homeassistant.components"] = components

    websocket_api = types.ModuleType("homeassistant.components.websocket_api")
    registered = []

    class ActiveConnection:
        pass

    def websocket_command(schema):
        schemas.append(schema)

        def decorator(func):
            return func

        return decorator

    websocket_api.ActiveConnection = ActiveConnection
    websocket_api.websocket_command = websocket_command
    websocket_api.async_response = lambda func: func
    websocket_api.async_register_command = lambda _hass, command: registered.append(command)
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
        "custom_components.frakon_energy.providers.mnd_source_proposals_ws_api",
        "custom_components/frakon_energy/providers/mnd_source_proposals_ws_api.py",
    )
    return (
        ws,
        registered,
        schemas,
        fetch_calls,
        contracts,
        sources,
        context,
        selection,
        download,
        resolver,
        proposals,
    )


class ConfigEntries:
    def __init__(self, entry):
        self.entry = entry
        self.updates = []

    def async_get_entry(self, entry_id):
        if self.entry is not None and self.entry.entry_id == entry_id:
            return self.entry
        return None

    def async_update_entry(self, entry, *, options):
        self.updates.append((entry, options))
        entry.options = options


class Hass:
    def __init__(self, entry):
        self.data = {"frakon_energy": {}}
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


def _entry(*, domain="frakon_energy", options=None):
    return types.SimpleNamespace(
        entry_id="entry-1",
        domain=domain,
        options={} if options is None else dict(options),
    )


def _propose_message(ws, **overrides):
    message = {
        "id": 1,
        "type": ws.COMMAND_MND_SOURCE_PROPOSE,
        "entry_id": "entry-1",
        "source_context": {"postcode": "412 01"},
        "product_name": "Proud - Ceník Říjen 28",
        "distributor": "cez_distribuce",
        "contract_kind": "fixed",
        "source_url": OFFICIAL_URL,
        "valid_from": "2026-06-11",
        "valid_to": "2028-10-31",
    }
    message.update(overrides)
    return message


def test_registration_is_idempotent_and_schemas_expose_no_price_or_client_sha_authority() -> None:
    ws, registered, schemas, *_ = load_module()
    hass = Hass(_entry())

    ws.async_register_mnd_source_proposals_websocket(hass)
    ws.async_register_mnd_source_proposals_websocket(hass)

    assert len(registered) == 2
    assert len(schemas) == 2
    assert set(schemas[0]) == {
        "type",
        "entry_id",
        "source_context",
        "product_name",
        "distributor",
        "contract_kind",
        "source_url",
        "valid_from",
        "valid_to",
    }
    assert "document_sha256" not in schemas[0]
    assert not any("price" in str(key) or "czk" in str(key) for key in schemas[0])
    assert set(schemas[1]) == {"type", "entry_id", "proposal_fingerprint"}


def test_propose_downloads_exact_official_pdf_hashes_server_side_and_never_leaks_postcode() -> None:
    (
        ws,
        registered,
        _schemas,
        fetch_calls,
        _contracts,
        _sources,
        _context,
        _selection,
        _download,
        _resolver,
        proposals,
    ) = load_module()
    entry = _entry()
    hass = Hass(entry)
    connection = Connection()
    ws.async_register_mnd_source_proposals_websocket(hass)

    asyncio.run(registered[0](hass, connection, _propose_message(ws)))

    assert connection.errors == []
    assert connection.admin_calls == 1
    assert len(fetch_calls) == 1
    candidate, request, checked_at = fetch_calls[0]
    assert candidate.document.source_url == OFFICIAL_URL
    assert candidate.document.sha256 is None
    assert request.source_url == OFFICIAL_URL
    assert request.allow_redirects is False
    assert checked_at == FIXED_NOW

    assert len(hass.config_entries.updates) == 1
    stored = proposals.mnd_source_proposals_from_options(entry.options)
    assert len(stored) == 1
    proposal = stored[0]
    assert proposal.document_sha256 == PDF_SHA256
    assert proposal.source_url == OFFICIAL_URL
    assert proposal.proposed_at == FIXED_NOW
    assert "41201" not in repr(entry.options)
    assert "412 01" not in repr(entry.options)

    payload = connection.results[0][1]
    assert payload["document_sha256"] == PDF_SHA256
    assert payload["download_performed"] is True
    assert payload["parsing_performed"] is False
    assert payload["confirmation_performed"] is False
    assert payload["activation_performed"] is False
    assert "postcode" not in repr(payload)
    assert "41201" not in repr(payload)
    assert not any("price" in key or "czk" in key for key in payload)


def test_missing_postcode_invalid_product_validity_or_non_mnd_url_reject_before_http() -> None:
    ws, registered, _schemas, fetch_calls, *_ = load_module()
    entry = _entry()
    hass = Hass(entry)
    ws.async_register_mnd_source_proposals_websocket(hass)

    for message in (
        _propose_message(ws, source_context={}),
        _propose_message(ws, product_name="Unknown product"),
        _propose_message(ws, valid_to="2028-12-31"),
        _propose_message(
            ws,
            source_url=f"https://example.com/documents/view/{DOCUMENT_UUID}",
        ),
    ):
        connection = Connection()
        asyncio.run(registered[0](hass, connection, message))
        assert connection.results == []
        assert connection.errors[0][1] == "invalid_mnd_source_proposal"

    assert fetch_calls == []
    assert hass.config_entries.updates == []


def test_download_failure_or_not_modified_never_persists_proposal() -> None:
    for mode in ("error", "not_modified"):
        ws, registered, _schemas, fetch_calls, *_ = load_module(download_mode=mode)
        entry = _entry()
        hass = Hass(entry)
        connection = Connection()
        ws.async_register_mnd_source_proposals_websocket(hass)

        asyncio.run(registered[0](hass, connection, _propose_message(ws)))

        assert len(fetch_calls) == 1
        assert connection.results == []
        assert connection.errors[0][1] == "mnd_source_download_failed"
        assert entry.options == {}
        assert hass.config_entries.updates == []


def test_confirmation_accepts_fingerprint_only_and_appends_sha_pinned_resolution() -> None:
    (
        ws,
        registered,
        _schemas,
        _fetch_calls,
        _contracts,
        _sources,
        _context,
        _selection,
        _download,
        resolver,
        proposals,
    ) = load_module()
    entry = _entry()
    hass = Hass(entry)
    propose_connection = Connection()
    ws.async_register_mnd_source_proposals_websocket(hass)
    asyncio.run(registered[0](hass, propose_connection, _propose_message(ws)))
    proposal_fingerprint = propose_connection.results[0][1]["proposal_fingerprint"]

    confirm_connection = Connection()
    asyncio.run(
        registered[1](
            hass,
            confirm_connection,
            {
                "id": 2,
                "type": ws.COMMAND_MND_SOURCE_CONFIRM,
                "entry_id": "entry-1",
                "proposal_fingerprint": proposal_fingerprint,
            },
        )
    )

    assert confirm_connection.errors == []
    confirmed = resolver.confirmed_mnd_source_resolutions_from_options(entry.options)
    assert len(confirmed) == 1
    resolution = confirmed[0]
    assert resolution.document_sha256 == PDF_SHA256
    assert resolution.source_url == OFFICIAL_URL
    assert "41201" not in repr(entry.options)
    assert proposals.mnd_source_proposals_from_options(entry.options)[0].fingerprint == (
        proposal_fingerprint
    )
    result = confirm_connection.results[0][1]
    assert result["confirmed"] is True
    assert result["parsing_performed"] is False
    assert result["activation_performed"] is False
    assert result["document_sha256"] == PDF_SHA256


def test_unknown_confirmation_fingerprint_fails_without_write() -> None:
    ws, registered, _schemas, _fetch_calls, *_ = load_module()
    entry = _entry()
    hass = Hass(entry)
    connection = Connection()
    ws.async_register_mnd_source_proposals_websocket(hass)

    asyncio.run(
        registered[1](
            hass,
            connection,
            {
                "id": 2,
                "type": ws.COMMAND_MND_SOURCE_CONFIRM,
                "entry_id": "entry-1",
                "proposal_fingerprint": "c" * 64,
            },
        )
    )

    assert connection.results == []
    assert connection.errors[0][1] == "mnd_source_proposal_not_found"
    assert hass.config_entries.updates == []


def test_wrong_entry_domain_is_rejected_before_any_network_access() -> None:
    ws, registered, _schemas, fetch_calls, *_ = load_module()
    entry = _entry(domain="other")
    hass = Hass(entry)
    connection = Connection()
    ws.async_register_mnd_source_proposals_websocket(hass)

    asyncio.run(registered[0](hass, connection, _propose_message(ws)))

    assert connection.results == []
    assert connection.errors[0][1] == "entry_not_found"
    assert fetch_calls == []
