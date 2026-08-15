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
    fetch_mode="success",
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

    class SupplierValue:
        def __init__(self, value):
            self.value = value

    class DistributorValue:
        def __init__(self, value):
            self.value = value

    @dataclass(frozen=True)
    class Breaker:
        code: str = "3x25A"

    @dataclass(frozen=True)
    class ElectricityContract:
        supplier: object
        distributor: object
        product_name: str
        distribution_tariff: str
        breaker: Breaker = Breaker()
        customer_confirmed: bool = False

        @classmethod
        def from_dict(cls, payload):
            calls.append(("contract", dict(payload)))
            return cls(
                supplier=SupplierValue(str(payload.get("supplier", "mnd"))),
                distributor=DistributorValue(
                    str(payload.get("distributor", "cez_distribuce"))
                ),
                product_name=str(
                    payload.get("product_name", "Proud - Ceník Říjen 28")
                ),
                distribution_tariff=str(
                    payload.get("distribution_tariff", "D25d")
                ),
                customer_confirmed=bool(
                    payload.get("customer_confirmed", False)
                ),
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
        proposal = Proposal()
        if already_staged:
            return dict(options), proposal
        updated = dict(options)
        updated["manual_proposal_saved"] = proposal.fingerprint
        return updated, proposal

    customer.stage_customer_tariff_proposal = stage_customer_tariff_proposal
    sys.modules[customer.__name__] = customer

    manual_preview = types.ModuleType(
        "custom_components.frakon_energy.manual_tariff_preview"
    )

    @dataclass(frozen=True)
    class ManualSupplierCommercialInput:
        high_rate_czk_per_kwh: Decimal
        low_rate_czk_per_kwh: Decimal
        supplier_standing_czk_month: Decimal

    class Preview:
        assembly = object()

        def as_dict(self):
            return {
                "authority_method": "manual_user_entry",
                "manual_entry": True,
                "parsing_performed": False,
                "persistence_performed": False,
                "activation_performed": False,
            }

    def build_manual_all_in_tariff_preview(**kwargs):
        calls.append(("manual_preview", kwargs))
        if preview_mode == "invalid":
            raise ValueError("manual preview mismatch")
        if preview_mode == "unsupported":
            raise LookupError("manual preview unsupported")
        return Preview()

    manual_preview.ManualSupplierCommercialInput = ManualSupplierCommercialInput
    manual_preview.build_manual_all_in_tariff_preview = (
        build_manual_all_in_tariff_preview
    )
    sys.modules[manual_preview.__name__] = manual_preview

    regulated = types.ModuleType(
        "custom_components.frakon_energy.regulated_catalog"
    )

    class RegulatedVersion:
        fingerprint = "e" * 64
        bundle = object()
        evidence = (object(),)

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
        contract,
        *,
        day,
        registry,
        source_context=None,
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

    def _registry_for_hass(hass):
        calls.append(("registry_base", hass))
        return base_registry

    def _registry_for_entry(hass, entry, *, registry=None):
        calls.append(("registry_entry", hass, entry, registry))
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

    fetch_module = types.ModuleType("custom_components.frakon_energy.tariff_fetch")

    class TariffNotModified:
        pass

    def build_tariff_fetch_request(selected, *, selected_fingerprint):
        calls.append(("request", selected, selected_fingerprint))
        return object()

    fetch_module.TariffNotModified = TariffNotModified
    fetch_module.build_tariff_fetch_request = build_tariff_fetch_request
    sys.modules[fetch_module.__name__] = fetch_module

    http = types.ModuleType("custom_components.frakon_energy.tariff_http_ha")

    async def async_fetch_selected_tariff_document_ha(
        hass,
        *,
        candidate,
        request,
        checked_at,
    ):
        calls.append(("fetch", candidate, request, checked_at))
        if fetch_mode == "not_modified":
            return TariffNotModified()
        if fetch_mode == "invalid":
            return object()
        if fetch_mode == "error":
            raise RuntimeError("network failure")
        return ValidatedTariffDownload()

    http.async_fetch_selected_tariff_document_ha = (
        async_fetch_selected_tariff_document_ha
    )
    sys.modules[http.__name__] = http

    context_module = types.ModuleType(
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
            unexpected = set(value) - {"postcode"}
            if unexpected:
                raise ValueError("source_context contains unsupported fields")
            postcode = value.get("postcode")
            if postcode not in (None, "41201"):
                raise ValueError("postcode must be a valid five-digit Czech PSČ")
            return cls(postcode=postcode)

        def as_dict(self):
            return {} if self.postcode is None else {"postcode": self.postcode}

    def tariff_source_context_fingerprint(context):
        calls.append(("context_fingerprint", context.postcode))
        return "1" * 64

    context_module.TariffSourceResolutionContext = TariffSourceResolutionContext
    context_module.tariff_source_context_fingerprint = (
        tariff_source_context_fingerprint
    )
    sys.modules[context_module.__name__] = context_module

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


def _entry(*, options=None):
    return types.SimpleNamespace(
        entry_id="entry-1",
        domain="frakon_energy",
        options={} if options is None else dict(options),
    )


def _message(*, manual=None, fingerprint=None, source_context=None):
    payload = {
        "id": 1,
        "type": "frakon_energy/tariff/customer/manual_propose",
        "entry_id": "entry-1",
        "contract": {
            "supplier": "mnd",
            "distributor": "cez_distribuce",
            "product_name": "Proud - Ceník Říjen 28",
            "distribution_tariff": "D25d",
            "customer_confirmed": True,
        },
        "day": "2026-08-15",
        "candidate_fingerprint": fingerprint or "d" * 64,
        "manual_commercial": manual
        if manual is not None
        else {
            "high_rate_czk_per_kwh": "2.899",
            "low_rate_czk_per_kwh": "2.899",
            "supplier_standing_czk_month": "168",
        },
    }
    if source_context is not None:
        payload["source_context"] = source_context
    return payload


def test_manual_websocket_has_only_propose_and_never_imports_parser_pipeline() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "tariff_parser_preview" not in source
    assert "tariff_pdf_text" not in source
    assert "parse_supplier_tariff_preview" not in source
    assert "extract_validated_tariff_pdf_text" not in source

    module, registered, schemas, _calls, _authority = load_module()
    hass = Hass(_entry())
    module.async_register_manual_customer_tariff_websocket(hass)
    module.async_register_manual_customer_tariff_websocket(hass)

    assert len(registered) == 1
    assert module.COMMAND_MANUAL_CUSTOMER_TARIFF_PROPOSE == (
        "frakon_energy/tariff/customer/manual_propose"
    )
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
        "bundle",
        "evidence",
        "all_in_vt_czk_kwh",
        "all_in_nt_czk_kwh",
        "fixed_monthly_total_czk",
        "proposal_fingerprint",
    ):
        assert forbidden not in schemas[0]


def test_mnd_manual_propose_uses_entry_registry_and_server_selected_manual_authority() -> None:
    module, registered, _schemas, calls, authority = load_module()
    entry = _entry(
        options={
            "confirmed_regulated_tariffs": ["fixture"],
            "mnd_confirmed_source_resolutions": ["entry-fixture"],
        }
    )
    hass = Hass(entry)
    connection = Connection()
    module.async_register_manual_customer_tariff_websocket(hass)

    asyncio.run(
        registered[0](
            hass,
            connection,
            _message(source_context={"postcode": "41201"}),
        )
    )

    assert connection.errors == []
    names = [item[0] for item in calls]
    assert names.index("regulated") < names.index("registry_entry")
    assert names.index("registry_entry") < names.index("discover")
    assert names.index("discover") < names.index("fetch")
    assert names.index("fetch") < names.index("manual_preview")
    assert names.index("manual_preview") < names.index("stage")

    discover = next(item for item in calls if item[0] == "discover")
    assert discover[4].postcode == "41201"
    stage = next(item for item in calls if item[0] == "stage")
    assert stage[2]["contract"].customer_confirmed is False
    assert stage[2]["candidate_fingerprint"] == "d" * 64
    assert stage[2]["regulated_version_fingerprint"] == "e" * 64
    assert (
        stage[2]["authority_method"]
        is authority.MANUAL_USER_ENTRY
    )

    manual_input = next(item for item in calls if item[0] == "manual_preview")[1][
        "manual_commercial"
    ]
    assert manual_input.high_rate_czk_per_kwh == Decimal("2.899")
    assert manual_input.low_rate_czk_per_kwh == Decimal("2.899")
    assert manual_input.supplier_standing_czk_month == Decimal("168")

    assert len(hass.config_entries.updates) == 1
    payload = connection.results[0][1]
    assert payload["proposal_fingerprint"] == "a" * 64
    assert payload["authority_method"] == "manual_user_entry"
    assert payload["manual_entry_performed"] is True
    assert payload["download_performed"] is True
    assert payload["parsing_performed"] is False
    assert payload["confirmation_performed"] is False
    assert payload["activation_performed"] is False
    assert payload["source_context_fingerprint"] == "1" * 64
    assert payload["source_url"].startswith(
        "https://prod.mnd.cz/documents/view/"
    )
    assert payload["document_sha256"] == "f" * 64


def test_manual_commercial_payload_is_the_only_client_price_authority() -> None:
    invalid_manual = (
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
            "high_rate_czk_per_kwh": "NaN",
            "low_rate_czk_per_kwh": "2.899",
            "supplier_standing_czk_month": "168",
        },
        {
            "high_rate_czk_per_kwh": "-1",
            "low_rate_czk_per_kwh": "2.899",
            "supplier_standing_czk_month": "168",
        },
    )

    for manual in invalid_manual:
        module, registered, _schemas, calls, _authority = load_module()
        hass = Hass(_entry())
        connection = Connection()
        module.async_register_manual_customer_tariff_websocket(hass)
        asyncio.run(
            registered[0](hass, connection, _message(manual=manual))
        )
        assert connection.errors[0][1] == "invalid_manual_commercial"
        assert not any(
            item[0]
            in {
                "regulated",
                "registry_entry",
                "discover",
                "fetch",
                "manual_preview",
                "stage",
            }
            for item in calls
        )
        assert hass.config_entries.updates == []


def test_regulator_candidate_and_download_failures_never_persist() -> None:
    for regulator_mode, expected_code in (
        ("missing", "regulated_tariff_not_available"),
        ("invalid", "regulated_tariff_invalid"),
    ):
        module, registered, _schemas, calls, _authority = load_module(
            regulator_mode=regulator_mode
        )
        hass = Hass(_entry())
        connection = Connection()
        module.async_register_manual_customer_tariff_websocket(hass)
        asyncio.run(registered[0](hass, connection, _message()))
        assert connection.errors[0][1] == expected_code
        assert not any(
            item[0] in {"registry_entry", "discover", "fetch", "stage"}
            for item in calls
        )
        assert hass.config_entries.updates == []

    module, registered, _schemas, calls, _authority = load_module()
    hass = Hass(_entry())
    connection = Connection()
    module.async_register_manual_customer_tariff_websocket(hass)
    asyncio.run(
        registered[0](
            hass,
            connection,
            _message(fingerprint="0" * 64),
        )
    )
    assert connection.errors[0][1] == "candidate_not_found"
    assert not any(item[0] in {"fetch", "manual_preview", "stage"} for item in calls)
    assert hass.config_entries.updates == []

    for fetch_mode, expected_code in (
        ("error", "download_failed"),
        ("not_modified", "not_modified_without_cached_document"),
        ("invalid", "download_failed"),
    ):
        module, registered, _schemas, calls, _authority = load_module(
            fetch_mode=fetch_mode
        )
        hass = Hass(_entry())
        connection = Connection()
        module.async_register_manual_customer_tariff_websocket(hass)
        asyncio.run(registered[0](hass, connection, _message()))
        assert connection.errors[0][1] == expected_code
        assert not any(item[0] in {"manual_preview", "stage"} for item in calls)
        assert hass.config_entries.updates == []


def test_preview_failures_and_repeated_stage_never_activate_or_churn_writes() -> None:
    for preview_mode, expected_code in (
        ("invalid", "manual_tariff_proposal_failed"),
        ("unsupported", "manual_tariff_not_supported"),
    ):
        module, registered, _schemas, _calls, _authority = load_module(
            preview_mode=preview_mode
        )
        hass = Hass(_entry())
        connection = Connection()
        module.async_register_manual_customer_tariff_websocket(hass)
        asyncio.run(registered[0](hass, connection, _message()))
        assert connection.errors[0][1] == expected_code
        assert hass.config_entries.updates == []

    module, registered, _schemas, _calls, _authority = load_module(
        already_staged=True
    )
    entry = _entry(options={"manual_proposal_saved": "a" * 64})
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
        item[0] in {"regulated", "discover", "fetch", "stage"}
        for item in calls
    )
    assert hass.config_entries.updates == []
