import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
import importlib.util
from pathlib import Path
import sys
import types


FIXED_NOW = datetime(2026, 8, 15, 18, 30, tzinfo=timezone.utc)
SOURCE = Path("custom_components/frakon_energy/manual_customer_tariff_ws_api.py")


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_module(
    *,
    regulator_mode="success",
    download_mode="success",
    preview_mode="success",
    already_staged=False,
):
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.all_in_authority",
        "custom_components.frakon_energy.const",
        "custom_components.frakon_energy.contracts",
        "custom_components.frakon_energy.customer_tariff_proposals",
        "custom_components.frakon_energy.manual_tariff_preview",
        "custom_components.frakon_energy.regulated_catalog",
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components.frakon_energy.tariff_discovery",
        "custom_components.frakon_energy.tariff_discovery_ws_api",
        "custom_components.frakon_energy.tariff_download",
        "custom_components.frakon_energy.tariff_fetch",
        "custom_components.frakon_energy.tariff_http_ha",
        "custom_components.frakon_energy.tariff_source_context",
        "custom_components.frakon_energy.manual_customer_tariff_ws_api",
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

    authority = types.ModuleType("custom_components.frakon_energy.all_in_authority")

    class AllInTariffAuthorityMethod(StrEnum):
        VERIFIED_PARSER = "verified_parser"
        MANUAL_USER_ENTRY = "manual_user_entry"

    authority.AllInTariffAuthorityMethod = AllInTariffAuthorityMethod
    sys.modules[authority.__name__] = authority

    const = types.ModuleType("custom_components.frakon_energy.const")
    const.DOMAIN = "frakon_energy"
    sys.modules[const.__name__] = const

    contracts = types.ModuleType("custom_components.frakon_energy.contracts")

    @dataclass(frozen=True)
    class Value:
        value: str

    @dataclass(frozen=True)
    class Breaker:
        code: str = "3x25A"

    @dataclass(frozen=True)
    class ElectricityContract:
        supplier: Value = Value("mnd")
        distributor: Value = Value("cez_distribuce")
        product_name: str = "Proud - Ceník Říjen 28"
        distribution_tariff: str = "D25d"
        breaker: Breaker = Breaker()
        customer_confirmed: bool = False

        @classmethod
        def from_dict(cls, payload):
            calls.append(("contract", dict(payload)))
            return cls(
                supplier=Value(str(payload.get("supplier", "mnd"))),
                distributor=Value(str(payload.get("distributor", "cez_distribuce"))),
                product_name=str(payload.get("product_name", "Proud - Ceník Říjen 28")),
                distribution_tariff=str(payload.get("distribution_tariff", "D25d")),
                customer_confirmed=bool(payload.get("customer_confirmed", False)),
            )

        def applies_on(self, _day):
            return True

    contracts.ElectricityContract = ElectricityContract
    sys.modules[contracts.__name__] = contracts

    customer = types.ModuleType(
        "custom_components.frakon_energy.customer_tariff_proposals"
    )

    @dataclass(frozen=True)
    class Proposal:
        fingerprint: str = "a" * 64
        contract_fingerprint: str = "b" * 64
        all_in_tariff_fingerprint: str = "c" * 64
        candidate_fingerprint: str = "d" * 64
        regulated_version_fingerprint: str = "e" * 64
        proposed_for_day: date = date(2026, 8, 15)
        proposed_at: datetime = FIXED_NOW

    def stage_customer_tariff_proposal(options, **kwargs):
        calls.append(("stage", dict(options), kwargs))
        assert (
            kwargs["authority_method"]
            is AllInTariffAuthorityMethod.MANUAL_USER_ENTRY
        )
        proposal = Proposal()
        if already_staged:
            return dict(options), proposal
        return {**dict(options), "proposal_saved": proposal.fingerprint}, proposal

    customer.stage_customer_tariff_proposal = stage_customer_tariff_proposal
    sys.modules[customer.__name__] = customer

    manual = types.ModuleType("custom_components.frakon_energy.manual_tariff_preview")

    @dataclass(frozen=True)
    class ManualSupplierCommercialInput:
        high_rate_czk_per_kwh: Decimal
        low_rate_czk_per_kwh: Decimal
        supplier_standing_czk_month: Decimal

    class Preview:
        authority_method = AllInTariffAuthorityMethod.MANUAL_USER_ENTRY
        parsing_performed = False
        persistence_performed = False
        activation_performed = False
        assembly = object()

        def as_dict(self):
            return {
                "authority_method": self.authority_method.value,
                "manual_entry": True,
                "parsing_performed": False,
                "persistence_performed": False,
                "activation_performed": False,
            }

    def build_manual_all_in_tariff_preview(**kwargs):
        calls.append(("preview", kwargs))
        if preview_mode == "unsupported":
            raise LookupError("manual supplier unsupported")
        if preview_mode == "invalid":
            raise ValueError("manual preview mismatch")
        preview = Preview()
        if preview_mode == "unsafe":
            preview.authority_method = AllInTariffAuthorityMethod.VERIFIED_PARSER
        return preview

    manual.ManualSupplierCommercialInput = ManualSupplierCommercialInput
    manual.build_manual_all_in_tariff_preview = build_manual_all_in_tariff_preview
    sys.modules[manual.__name__] = manual

    regulated = types.ModuleType("custom_components.frakon_energy.regulated_catalog")

    class RegulatedVersion:
        fingerprint = "e" * 64
        bundle = object()
        evidence = ()

    def select_confirmed_regulated_tariff_for_day(*args, **kwargs):
        calls.append(("regulated", kwargs))
        if regulator_mode == "missing":
            raise LookupError("no confirmed regulator")
        if regulator_mode == "invalid":
            raise ValueError("ambiguous regulator")
        return RegulatedVersion()

    regulated.select_confirmed_regulated_tariff_for_day = (
        select_confirmed_regulated_tariff_for_day
    )
    sys.modules[regulated.__name__] = regulated

    selection = types.ModuleType(
        "custom_components.frakon_energy.tariff_candidate_selection"
    )
    candidate = types.SimpleNamespace(name="mnd-candidate")

    def select_tariff_candidate(candidates, *, fingerprint):
        calls.append(("select", candidates, fingerprint))
        if fingerprint != "d" * 64:
            raise LookupError("candidate not found")
        return candidate

    selection.select_tariff_candidate = select_tariff_candidate
    sys.modules[selection.__name__] = selection

    discovery = types.ModuleType("custom_components.frakon_energy.tariff_discovery")

    async def async_discover_contract_tariff_candidates(
        contract, *, day, registry, source_context
    ):
        calls.append(("discover", contract, day, registry, source_context))
        return (candidate,)

    discovery.async_discover_contract_tariff_candidates = (
        async_discover_contract_tariff_candidates
    )
    sys.modules[discovery.__name__] = discovery

    discovery_ws = types.ModuleType(
        "custom_components.frakon_energy.tariff_discovery_ws_api"
    )
    base_registry = object()
    entry_registry = object()

    def _registry_for_hass(_hass):
        calls.append(("base_registry", base_registry))
        return base_registry

    def _registry_for_entry(hass, entry, *, registry):
        calls.append(("entry_registry", entry.entry_id, registry))
        assert registry is base_registry
        return entry_registry

    discovery_ws._registry_for_hass = _registry_for_hass
    discovery_ws._registry_for_entry = _registry_for_entry
    sys.modules[discovery_ws.__name__] = discovery_ws

    download_module = types.ModuleType(
        "custom_components.frakon_energy.tariff_download"
    )

    class ValidatedTariffDownload:
        def __init__(self):
            self.selected_fingerprint = "d" * 64
            self.validated_at = FIXED_NOW
            self.document = types.SimpleNamespace(
                source_url=(
                    "https://prod.mnd.cz/documents/view/"
                    "12345678-1234-4234-8234-123456789abc"
                ),
                sha256="f" * 64,
            )
            self.content = b"%PDF fixture"

    download_module.ValidatedTariffDownload = ValidatedTariffDownload
    sys.modules[download_module.__name__] = download_module

    fetch = types.ModuleType("custom_components.frakon_energy.tariff_fetch")

    class TariffNotModified:
        pass

    def build_tariff_fetch_request(selected, *, selected_fingerprint):
        calls.append(("request", selected, selected_fingerprint))
        return object()

    fetch.TariffNotModified = TariffNotModified
    fetch.build_tariff_fetch_request = build_tariff_fetch_request
    sys.modules[fetch.__name__] = fetch

    http = types.ModuleType("custom_components.frakon_energy.tariff_http_ha")

    async def async_fetch_selected_tariff_document_ha(
        hass, *, candidate, request, checked_at
    ):
        calls.append(("fetch", candidate, request, checked_at))
        if download_mode == "not_modified":
            return TariffNotModified()
        if download_mode == "invalid":
            return object()
        if download_mode == "error":
            raise RuntimeError("network failed")
        return ValidatedTariffDownload()

    http.async_fetch_selected_tariff_document_ha = (
        async_fetch_selected_tariff_document_ha
    )
    sys.modules[http.__name__] = http

    context = types.ModuleType(
        "custom_components.frakon_energy.tariff_source_context"
    )

    @dataclass(frozen=True)
    class TariffSourceResolutionContext:
        postcode: str | None = None

        @classmethod
        def from_value(cls, value):
            if value is None:
                return cls()
            if not isinstance(value, dict):
                raise ValueError("source_context must be an object")
            if set(value) - {"postcode"}:
                raise ValueError("source_context contains unsupported fields")
            postcode = value.get("postcode")
            if postcode not in (None, "41201"):
                raise ValueError("postcode must be a valid five-digit Czech PSČ")
            return cls(postcode=postcode)

    def tariff_source_context_fingerprint(value):
        calls.append(("context_fingerprint", value.postcode))
        return "1" * 64

    context.TariffSourceResolutionContext = TariffSourceResolutionContext
    context.tariff_source_context_fingerprint = tariff_source_context_fingerprint
    sys.modules[context.__name__] = context

    schemas = []
    registered = []
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

    class ActiveConnection:
        pass

    def websocket_command(schema):
        schemas.append(schema)

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
    websocket_api.async_register_command = lambda _hass, command: registered.append(command)
    sys.modules[websocket_api.__name__] = websocket_api
    components.websocket_api = websocket_api

    core = types.ModuleType("homeassistant.core")
    core.callback = lambda func: func

    class HomeAssistant:
        pass

    core.HomeAssistant = HomeAssistant
    sys.modules[core.__name__] = core

    util = types.ModuleType("homeassistant.util")
    util.__path__ = []
    sys.modules[util.__name__] = util
    dt = types.ModuleType("homeassistant.util.dt")
    dt.now = lambda: FIXED_NOW
    sys.modules[dt.__name__] = dt
    util.dt = dt

    module = _load(
        "custom_components.frakon_energy.manual_customer_tariff_ws_api",
        "custom_components/frakon_energy/manual_customer_tariff_ws_api.py",
    )
    return module, registered, schemas, calls, AllInTariffAuthorityMethod


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


def _entry(*, options=None, domain="frakon_energy"):
    return types.SimpleNamespace(
        entry_id="entry-1",
        domain=domain,
        options={} if options is None else dict(options),
    )


def _message(**overrides):
    msg = {
        "id": 1,
        "type": "frakon_energy/tariff/customer/manual/propose",
        "entry_id": "entry-1",
        "contract": {
            "supplier": "mnd",
            "distributor": "cez_distribuce",
            "product_name": "Proud - Ceník Říjen 28",
            "distribution_tariff": "D25d",
            "customer_confirmed": True,
        },
        "day": "2026-08-15",
        "candidate_fingerprint": "d" * 64,
        "manual_commercial": {
            "high_rate_czk_per_kwh": "2.899",
            "low_rate_czk_per_kwh": "2.899",
            "supplier_standing_czk_month": "168",
        },
        "source_context": {"postcode": "41201"},
    }
    msg.update(overrides)
    return msg


def test_registration_is_propose_only_and_has_no_parser_or_confirmation_boundary() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "tariff_parser_preview" not in source
    assert "tariff_pdf_text" not in source
    assert "manual/confirm" not in source
    assert "confirm_customer_tariff_proposal" not in source

    module, registered, schemas, _calls, _authority = load_module()
    hass = Hass(_entry())
    module.async_register_manual_customer_tariff_websocket(hass)
    module.async_register_manual_customer_tariff_websocket(hass)

    assert len(registered) == 1
    assert set(schemas[0]) == {
        "type",
        "entry_id",
        "contract",
        "day",
        "candidate_fingerprint",
        "manual_commercial",
        "source_context",
    }
    for forbidden in (
        "authority_method",
        "source_url",
        "document_sha256",
        "regulated",
        "regulated_evidence",
        "all_in_vt_czk_kwh",
        "all_in_nt_czk_kwh",
        "fixed_monthly_total_czk",
        "parser_name",
        "includes_vat",
    ):
        assert forbidden not in schemas[0]


def test_mnd_manual_propose_uses_entry_registry_and_server_selected_authority() -> None:
    module, registered, _schemas, calls, authority = load_module()
    entry = _entry(options={"confirmed_regulated_tariffs": ["fixture"]})
    hass = Hass(entry)
    connection = Connection()
    module.async_register_manual_customer_tariff_websocket(hass)

    asyncio.run(registered[0](hass, connection, _message()))

    assert connection.errors == []
    names = [item[0] for item in calls]
    assert names.index("regulated") < names.index("entry_registry")
    assert names.index("entry_registry") < names.index("discover") < names.index("fetch")
    assert names.index("fetch") < names.index("preview") < names.index("stage")
    stage = next(item for item in calls if item[0] == "stage")
    assert stage[2]["contract"].customer_confirmed is False
    assert stage[2]["authority_method"] is authority.MANUAL_USER_ENTRY
    manual_input = next(item for item in calls if item[0] == "preview")[1][
        "manual_commercial"
    ]
    assert manual_input.high_rate_czk_per_kwh == Decimal("2.899")
    assert manual_input.low_rate_czk_per_kwh == Decimal("2.899")
    assert manual_input.supplier_standing_czk_month == Decimal("168")
    assert len(hass.config_entries.updates) == 1
    payload = connection.results[0][1]
    assert payload["authority_method"] == "manual_user_entry"
    assert payload["manual_entry"] is True
    assert payload["download_performed"] is True
    assert payload["parsing_performed"] is False
    assert payload["confirmation_performed"] is False
    assert payload["activation_performed"] is False
    assert payload["source_context_fingerprint"] == "1" * 64


def test_manual_commercial_payload_is_exactly_three_plain_decimal_strings() -> None:
    invalid = (
        {
            "high_rate_czk_per_kwh": "2.899",
            "supplier_standing_czk_month": "168",
        },
        {
            "high_rate_czk_per_kwh": "2.899",
            "low_rate_czk_per_kwh": "2.899",
            "supplier_standing_czk_month": "168",
            "authority_method": "verified_parser",
        },
        {
            "high_rate_czk_per_kwh": 2.899,
            "low_rate_czk_per_kwh": "2.899",
            "supplier_standing_czk_month": "168",
        },
        {
            "high_rate_czk_per_kwh": "2.899e0",
            "low_rate_czk_per_kwh": "2.899",
            "supplier_standing_czk_month": "168",
        },
        {
            "high_rate_czk_per_kwh": "-1",
            "low_rate_czk_per_kwh": "2.899",
            "supplier_standing_czk_month": "168",
        },
    )
    for manual in invalid:
        module, registered, _schemas, calls, _authority = load_module()
        hass = Hass(_entry())
        connection = Connection()
        module.async_register_manual_customer_tariff_websocket(hass)
        msg = _message()
        msg["manual_commercial"] = manual
        asyncio.run(registered[0](hass, connection, msg))
        assert connection.errors[0][1] == "invalid_manual_commercial"
        assert not any(
            item[0] in {"regulated", "discover", "fetch", "preview", "stage"}
            for item in calls
        )


def test_regulator_candidate_and_download_failures_never_persist() -> None:
    module, registered, _schemas, calls, _authority = load_module(
        regulator_mode="missing"
    )
    hass = Hass(_entry())
    connection = Connection()
    module.async_register_manual_customer_tariff_websocket(hass)
    asyncio.run(registered[0](hass, connection, _message()))
    assert connection.errors[0][1] == "regulated_tariff_not_available"
    assert not any(item[0] in {"discover", "fetch", "preview", "stage"} for item in calls)

    module, registered, _schemas, calls, _authority = load_module()
    hass = Hass(_entry())
    connection = Connection()
    module.async_register_manual_customer_tariff_websocket(hass)
    asyncio.run(
        registered[0](
            hass,
            connection,
            _message(candidate_fingerprint="0" * 64),
        )
    )
    assert connection.errors[0][1] == "candidate_not_found"
    assert not any(item[0] in {"fetch", "preview", "stage"} for item in calls)

    module, registered, _schemas, calls, _authority = load_module(
        download_mode="not_modified"
    )
    hass = Hass(_entry())
    connection = Connection()
    module.async_register_manual_customer_tariff_websocket(hass)
    asyncio.run(registered[0](hass, connection, _message()))
    assert connection.errors[0][1] == "not_modified_without_cached_document"
    assert not any(item[0] in {"preview", "stage"} for item in calls)


def test_preview_failure_and_repeated_stage_never_activate_or_churn_writes() -> None:
    for mode, expected in (
        ("unsupported", "manual_tariff_not_supported"),
        ("invalid", "manual_tariff_proposal_failed"),
        ("unsafe", "manual_tariff_proposal_failed"),
    ):
        module, registered, _schemas, calls, _authority = load_module(
            preview_mode=mode
        )
        hass = Hass(_entry())
        connection = Connection()
        module.async_register_manual_customer_tariff_websocket(hass)
        asyncio.run(registered[0](hass, connection, _message()))
        assert connection.errors[0][1] == expected
        assert not any(item[0] == "stage" for item in calls)

    module, registered, _schemas, _calls, _authority = load_module(
        already_staged=True
    )
    entry = _entry(options={"proposal_saved": "a" * 64})
    hass = Hass(entry)
    connection = Connection()
    module.async_register_manual_customer_tariff_websocket(hass)
    asyncio.run(registered[0](hass, connection, _message()))
    assert connection.errors == []
    assert hass.config_entries.updates == []
    payload = connection.results[0][1]
    assert payload["persistence_performed"] is False
    assert payload["confirmation_performed"] is False
    assert payload["activation_performed"] is False


def test_invalid_source_context_stops_before_regulator_or_network() -> None:
    module, registered, _schemas, calls, _authority = load_module()
    hass = Hass(_entry())
    connection = Connection()
    module.async_register_manual_customer_tariff_websocket(hass)
    asyncio.run(
        registered[0](
            hass,
            connection,
            _message(source_context={"postcode": "99999"}),
        )
    )
    assert connection.errors[0][1] == "invalid_source_context"
    assert not any(
        item[0] in {"regulated", "discover", "fetch", "preview", "stage"}
        for item in calls
    )
