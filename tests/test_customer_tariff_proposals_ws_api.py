import asyncio
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import importlib.util
from pathlib import Path
import sys
import types


FIXED_NOW = datetime(2026, 8, 14, 20, 30, tzinfo=timezone.utc)


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_module(*, supplier="cez", regulator_mode="success", confirm_mode="success", already_staged=False, already_confirmed=False):
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.const",
        "custom_components.frakon_energy.contracts",
        "custom_components.frakon_energy.customer_tariff_proposals",
        "custom_components.frakon_energy.regulated_catalog",
        "custom_components.frakon_energy.tariff_all_in_preview",
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components.frakon_energy.tariff_discovery",
        "custom_components.frakon_energy.tariff_discovery_ws_api",
        "custom_components.frakon_energy.tariff_download",
        "custom_components.frakon_energy.tariff_fetch",
        "custom_components.frakon_energy.tariff_http_ha",
        "custom_components.frakon_energy.tariff_parser_preview",
        "custom_components.frakon_energy.tariff_pdf_text",
        "custom_components.frakon_energy.customer_tariff_proposals_ws_api",
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

    calls = []

    const = types.ModuleType("custom_components.frakon_energy.const")
    const.DOMAIN = "frakon_energy"
    sys.modules[const.__name__] = const

    contracts = types.ModuleType("custom_components.frakon_energy.contracts")

    class SupplierValue:
        def __init__(self, value):
            self.value = value

    class Supplier:
        CEZ = SupplierValue("cez")
        EON = SupplierValue("eon")

    @dataclass(frozen=True)
    class Breaker:
        code: str = "3x25A"

    @dataclass(frozen=True)
    class Distributor:
        value: str = "cez_distribuce"

    @dataclass(frozen=True)
    class ElectricityContract:
        supplier: object
        distributor: Distributor = Distributor()
        distribution_tariff: str = "D25d"
        breaker: Breaker = Breaker()
        product_name: str = "Elektřina na 3 roky"
        customer_confirmed: bool = False

        @classmethod
        def from_dict(cls, payload):
            calls.append(("contract", dict(payload)))
            chosen = Supplier.CEZ if payload.get("supplier", supplier) == "cez" else Supplier.EON
            return cls(
                supplier=chosen,
                customer_confirmed=bool(payload.get("customer_confirmed", False)),
            )

        def applies_on(self, _day):
            return True

    contracts.ElectricityContract = ElectricityContract
    contracts.Supplier = Supplier
    sys.modules[contracts.__name__] = contracts

    customer = types.ModuleType("custom_components.frakon_energy.customer_tariff_proposals")

    class Proposal:
        fingerprint = "a" * 64
        contract_fingerprint = "b" * 64
        all_in_tariff_fingerprint = "c" * 64
        candidate_fingerprint = "d" * 64
        regulated_version_fingerprint = "e" * 64
        proposed_for_day = date(2026, 8, 14)
        proposed_at = FIXED_NOW

    def stage_customer_tariff_proposal(options, **kwargs):
        calls.append(("stage", dict(options), kwargs))
        if already_staged:
            return dict(options), Proposal()
        return {**dict(options), "proposal_saved": Proposal.fingerprint}, Proposal()

    def confirm_customer_tariff_proposal(options, fingerprint):
        calls.append(("confirm", dict(options), fingerprint))
        if confirm_mode == "missing":
            raise LookupError("customer tariff proposal not found")
        if confirm_mode == "invalid":
            raise ValueError("customer tariff proposal linkage mismatch")
        if already_confirmed:
            return dict(options), Proposal()
        return {**dict(options), "customer_tariff_confirmed": fingerprint}, Proposal()

    customer.stage_customer_tariff_proposal = stage_customer_tariff_proposal
    customer.confirm_customer_tariff_proposal = confirm_customer_tariff_proposal
    sys.modules[customer.__name__] = customer

    regulated_catalog = types.ModuleType("custom_components.frakon_energy.regulated_catalog")

    class ConfirmedRegulatedTariffVersion:
        fingerprint = "e" * 64
        bundle = object()
        evidence = ()

    def select_confirmed_regulated_tariff_for_day(*args, **kwargs):
        calls.append(("regulated", kwargs))
        if regulator_mode == "missing":
            raise LookupError("no confirmed regulator")
        if regulator_mode == "invalid":
            raise ValueError("ambiguous regulator")
        return ConfirmedRegulatedTariffVersion()

    regulated_catalog.ConfirmedRegulatedTariffVersion = ConfirmedRegulatedTariffVersion
    regulated_catalog.select_confirmed_regulated_tariff_for_day = select_confirmed_regulated_tariff_for_day
    sys.modules[regulated_catalog.__name__] = regulated_catalog

    preview_module = types.ModuleType("custom_components.frakon_energy.tariff_all_in_preview")

    class AllInTariffPreview:
        def __init__(self):
            self.assembly = object()

        def as_dict(self):
            return {
                "all_in_vt_czk_kwh": "5.00",
                "persistence_performed": False,
                "activation_performed": False,
            }

    def build_all_in_tariff_preview(**kwargs):
        calls.append(("assemble", kwargs))
        return AllInTariffPreview()

    preview_module.AllInTariffPreview = AllInTariffPreview
    preview_module.build_all_in_tariff_preview = build_all_in_tariff_preview
    sys.modules[preview_module.__name__] = preview_module

    selection = types.ModuleType("custom_components.frakon_energy.tariff_candidate_selection")
    candidate = types.SimpleNamespace(name="candidate")

    def select_tariff_candidate(candidates, *, fingerprint):
        calls.append(("select", candidates, fingerprint))
        if fingerprint != "d" * 64:
            raise LookupError("candidate not found")
        return candidate

    selection.select_tariff_candidate = select_tariff_candidate
    sys.modules[selection.__name__] = selection

    discovery = types.ModuleType("custom_components.frakon_energy.tariff_discovery")

    async def async_discover_contract_tariff_candidates(contract, *, day, registry):
        calls.append(("discover", contract, day, registry))
        return (candidate,)

    discovery.async_discover_contract_tariff_candidates = async_discover_contract_tariff_candidates
    sys.modules[discovery.__name__] = discovery

    discovery_ws = types.ModuleType("custom_components.frakon_energy.tariff_discovery_ws_api")
    registry = object()
    discovery_ws._registry_for_hass = lambda _hass: registry
    sys.modules[discovery_ws.__name__] = discovery_ws

    download_module = types.ModuleType("custom_components.frakon_energy.tariff_download")

    class ValidatedTariffDownload:
        def __init__(self):
            self.selected_fingerprint = "d" * 64
            self.validated_at = FIXED_NOW
            self.document = types.SimpleNamespace(
                source_url="https://www.cez.cz/file/edee/cenik.pdf",
                sha256="f" * 64,
            )
            self.content = b"%PDF"

    download_module.ValidatedTariffDownload = ValidatedTariffDownload
    sys.modules[download_module.__name__] = download_module

    fetch_module = types.ModuleType("custom_components.frakon_energy.tariff_fetch")

    class TariffNotModified:
        pass

    def build_tariff_fetch_request(selected, *, selected_fingerprint):
        calls.append(("request", selected, selected_fingerprint))
        return object()

    fetch_module.TariffNotModified = TariffNotModified
    fetch_module.build_tariff_fetch_request = build_tariff_fetch_request
    sys.modules[fetch_module.__name__] = fetch_module

    http_ha = types.ModuleType("custom_components.frakon_energy.tariff_http_ha")

    async def async_fetch_selected_tariff_document_ha(hass, *, candidate, request, checked_at):
        calls.append(("fetch", candidate, request, checked_at))
        return ValidatedTariffDownload()

    http_ha.async_fetch_selected_tariff_document_ha = async_fetch_selected_tariff_document_ha
    sys.modules[http_ha.__name__] = http_ha

    parser = types.ModuleType("custom_components.frakon_energy.tariff_parser_preview")
    parser.parse_supplier_tariff_preview = lambda *args: calls.append(("parse", args)) or object()
    sys.modules[parser.__name__] = parser

    pdf = types.ModuleType("custom_components.frakon_energy.tariff_pdf_text")
    pdf.extract_validated_tariff_pdf_text = lambda download: calls.append(("extract", download)) or "text"
    sys.modules[pdf.__name__] = pdf

    schemas = []
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
        "custom_components.frakon_energy.customer_tariff_proposals_ws_api",
        "custom_components/frakon_energy/customer_tariff_proposals_ws_api.py",
    )
    return ws, registered, schemas, calls


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

    async def async_add_executor_job(self, func, *args):
        return func(*args)


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


def _propose_message(*, supplier="cez", confirmed=True):
    return {
        "id": 1,
        "type": "frakon_energy/tariff/customer/propose",
        "entry_id": "entry-1",
        "contract": {"supplier": supplier, "customer_confirmed": confirmed},
        "day": "2026-08-14",
        "candidate_fingerprint": "d" * 64,
    }


def test_registration_is_idempotent_and_schemas_expose_no_price_or_url_authority() -> None:
    ws, registered, schemas, _calls = load_module()
    hass = Hass(_entry())

    ws.async_register_customer_tariff_proposals_websocket(hass)
    ws.async_register_customer_tariff_proposals_websocket(hass)

    assert len(registered) == 2
    assert set(schemas[0]) == {"type", "entry_id", "contract", "day", "candidate_fingerprint"}
    assert set(schemas[1]) == {"type", "entry_id", "proposal_fingerprint"}
    for forbidden in ("source_url", "price", "bundle", "evidence", "all_in_vt_czk_kwh"):
        assert forbidden not in schemas[0]
        assert forbidden not in schemas[1]


def test_propose_resolves_regulator_before_discovery_and_strips_ui_confirmation() -> None:
    ws, registered, _schemas, calls = load_module()
    entry = _entry(options={"confirmed_regulated_tariffs": ["fixture"]})
    hass = Hass(entry)
    connection = Connection()
    ws.async_register_customer_tariff_proposals_websocket(hass)

    asyncio.run(registered[0](hass, connection, _propose_message(confirmed=True)))

    assert connection.errors == []
    names = [item[0] for item in calls]
    assert names.index("regulated") < names.index("discover") < names.index("fetch")
    stage_call = next(item for item in calls if item[0] == "stage")
    assert stage_call[2]["contract"].customer_confirmed is False
    assert stage_call[2]["candidate_fingerprint"] == "d" * 64
    assert stage_call[2]["regulated_version_fingerprint"] == "e" * 64
    assert len(hass.config_entries.updates) == 1
    payload = connection.results[0][1]
    assert payload["proposal_fingerprint"] == "a" * 64
    assert payload["persistence_performed"] is True
    assert payload["confirmation_performed"] is False
    assert payload["activation_performed"] is False
    assert payload["preview"]["activation_performed"] is False


def test_missing_regulator_and_unsupported_supplier_fail_before_network_or_persistence() -> None:
    ws, registered, _schemas, calls = load_module(regulator_mode="missing")
    hass = Hass(_entry())
    connection = Connection()
    ws.async_register_customer_tariff_proposals_websocket(hass)
    asyncio.run(registered[0](hass, connection, _propose_message()))
    assert connection.errors[0][1] == "regulated_tariff_not_available"
    assert not any(item[0] in {"discover", "fetch", "stage"} for item in calls)
    assert hass.config_entries.updates == []

    ws, registered, _schemas, calls = load_module(supplier="eon")
    hass = Hass(_entry())
    connection = Connection()
    ws.async_register_customer_tariff_proposals_websocket(hass)
    asyncio.run(registered[0](hass, connection, _propose_message(supplier="eon")))
    assert connection.errors[0][1] == "parser_not_supported"
    assert not any(item[0] in {"regulated", "discover", "fetch", "stage"} for item in calls)
    assert hass.config_entries.updates == []


def test_repeated_propose_has_no_write_churn_and_never_activates() -> None:
    ws, registered, _schemas, _calls = load_module(already_staged=True)
    entry = _entry(options={"proposal_saved": "a" * 64})
    hass = Hass(entry)
    connection = Connection()
    ws.async_register_customer_tariff_proposals_websocket(hass)

    asyncio.run(registered[0](hass, connection, _propose_message()))

    assert hass.config_entries.updates == []
    payload = connection.results[0][1]
    assert payload["persistence_performed"] is False
    assert payload["confirmation_performed"] is False
    assert payload["activation_performed"] is False


def test_confirm_uses_only_stored_proposal_fingerprint_and_marks_activation_on_write() -> None:
    ws, registered, schemas, calls = load_module()
    entry = _entry(options={"proposal_saved": "a" * 64})
    hass = Hass(entry)
    connection = Connection()
    ws.async_register_customer_tariff_proposals_websocket(hass)

    message = {
        "id": 2,
        "type": ws.COMMAND_CUSTOMER_TARIFF_CONFIRM,
        "entry_id": "entry-1",
        "proposal_fingerprint": "a" * 64,
    }
    asyncio.run(registered[1](hass, connection, message))

    assert set(schemas[1]) == {"type", "entry_id", "proposal_fingerprint"}
    assert calls[-1] == ("confirm", {"proposal_saved": "a" * 64}, "a" * 64)
    assert len(hass.config_entries.updates) == 1
    payload = connection.results[0][1]
    assert payload["confirmed"] is True
    assert payload["confirmation_performed"] is True
    assert payload["activation_performed"] is True


def test_repeated_or_invalid_confirmation_cannot_create_write_churn() -> None:
    ws, registered, _schemas, _calls = load_module(already_confirmed=True)
    entry = _entry(options={"customer_tariff_confirmed": "a" * 64})
    hass = Hass(entry)
    connection = Connection()
    ws.async_register_customer_tariff_proposals_websocket(hass)
    asyncio.run(
        registered[1](
            hass,
            connection,
            {
                "id": 3,
                "type": ws.COMMAND_CUSTOMER_TARIFF_CONFIRM,
                "entry_id": "entry-1",
                "proposal_fingerprint": "a" * 64,
            },
        )
    )
    assert hass.config_entries.updates == []
    assert connection.results[0][1]["activation_performed"] is False

    for mode, code in (
        ("missing", "customer_tariff_proposal_not_found"),
        ("invalid", "customer_tariff_confirmation_failed"),
    ):
        ws, registered, _schemas, _calls = load_module(confirm_mode=mode)
        hass = Hass(_entry())
        connection = Connection()
        ws.async_register_customer_tariff_proposals_websocket(hass)
        asyncio.run(
            registered[1](
                hass,
                connection,
                {
                    "id": 4,
                    "type": ws.COMMAND_CUSTOMER_TARIFF_CONFIRM,
                    "entry_id": "entry-1",
                    "proposal_fingerprint": "a" * 64,
                },
            )
        )
        assert connection.errors[0][1] == code
        assert hass.config_entries.updates == []
