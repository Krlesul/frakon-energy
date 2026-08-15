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


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_module(
    *,
    regulator_mode="success",
    discovery_mode="success",
    download_mode="success",
    authority_mode="manual",
    already_confirmed=False,
    unsafe_preview=False,
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

    authority_module = types.ModuleType(
        "custom_components.frakon_energy.all_in_authority"
    )

    class AllInTariffAuthorityMethod(StrEnum):
        VERIFIED_PARSER = "verified_parser"
        MANUAL_USER_ENTRY = "manual_user_entry"

    @dataclass(frozen=True)
    class Authority:
        method: AllInTariffAuthorityMethod

    def all_in_tariff_authority_from_options(options, fingerprint):
        calls.append(("authority", fingerprint, dict(options)))
        mode = options.get("authority_mode", authority_mode)
        if mode == "missing":
            raise LookupError("all-in tariff authority not found")
        if mode == "verified":
            return Authority(AllInTariffAuthorityMethod.VERIFIED_PARSER)
        return Authority(AllInTariffAuthorityMethod.MANUAL_USER_ENTRY)

    authority_module.AllInTariffAuthorityMethod = AllInTariffAuthorityMethod
    authority_module.all_in_tariff_authority_from_options = (
        all_in_tariff_authority_from_options
    )
    sys.modules[authority_module.__name__] = authority_module

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
        product_name: str = "Proud - Ceník Říjen 28"
        distribution_tariff: str = "D25d"
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

    def customer_tariff_proposals_from_options(options):
        stored = options.get("stored_proposal")
        return () if stored is None else (stored,)

    def stage_customer_tariff_proposal(options, **kwargs):
        calls.append(("stage", dict(options), kwargs))
        assert kwargs["authority_method"] is AllInTariffAuthorityMethod.MANUAL_USER_ENTRY
        proposal = Proposal()
        updated = dict(options)
        updated["stored_proposal"] = proposal
        updated["authority_mode"] = "manual"
        updated["proposal_saved"] = proposal.fingerprint
        return updated, proposal

    def confirm_customer_tariff_proposal(options, fingerprint):
        calls.append(("confirm", dict(options), fingerprint))
        proposal = options.get("stored_proposal")
        if proposal is None or proposal.fingerprint != fingerprint:
            raise LookupError("customer tariff proposal not found")
        if already_confirmed:
            return dict(options), proposal
        updated = dict(options)
        updated["confirmed"] = fingerprint
        return updated, proposal

    customer.Proposal = Proposal
    customer.customer_tariff_proposals_from_options = (
        customer_tariff_proposals_from_options
    )
    customer.stage_customer_tariff_proposal = stage_customer_tariff_proposal
    customer.confirm_customer_tariff_proposal = confirm_customer_tariff_proposal
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
        authority_method = (
            AllInTariffAuthorityMethod.VERIFIED_PARSER
            if unsafe_preview
            else AllInTariffAuthorityMethod.MANUAL_USER_ENTRY
        )
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
        calls.append(("manual_preview", kwargs))
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
        if discovery_mode == "missing":
            return ()
        if discovery_mode == "error":
            raise ValueError("invalid discovery")
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
            if set(value) - {"postcode"}:
                raise ValueError("source_context contains unsupported fields")
            postcode = value.get("postcode")
            if postcode is not None and postcode != "41201":
                raise ValueError("postcode must be a valid five-digit Czech PSČ")
            return cls(postcode=postcode)

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

    module = _load(
        "custom_components.frakon_energy.manual_customer_tariff_ws_api",
        "custom_components/frakon_energy/manual_customer_tariff_ws_api.py",
    )
    return module, registered, schemas, calls, customer.Proposal


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


def _propose_message(**overrides):
    message = {
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
    message.update(overrides)
    return message


def _confirm_message(fingerprint="a" * 64):
    return {
        "id": 2,
        "type": "frakon_energy/tariff/customer/manual/confirm",
        "entry_id": "entry-1",
        "proposal_fingerprint": fingerprint,
    }


def test_registration_is_idempotent_and_client_cannot_supply_authority_sources_or_totals() -> None:
    module, registered, schemas, _calls, _proposal = load_module()
    hass = Hass(_entry())

    module.async_register_manual_customer_tariff_websocket(hass)
    module.async_register_manual_customer_tariff_websocket(hass)

    assert module.COMMAND_MANUAL_CUSTOMER_TARIFF_PROPOSE.endswith("/manual/propose")
    assert module.COMMAND_MANUAL_CUSTOMER_TARIFF_CONFIRM.endswith("/manual/confirm")
    assert len(registered) == 2
    assert set(schemas[0]) == {
        "type",
        "entry_id",
        "contract",
        "day",
        "candidate_fingerprint",
        "manual_commercial",
        "source_context",
    }
    assert set(schemas[1]) == {"type", "entry_id", "proposal_fingerprint"}
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
        assert forbidden not in schemas[1]


def test_mnd_manual_propose_uses_entry_registry_regulator_before_network_and_never_parses() -> None:
    module, registered, _schemas, calls, _proposal = load_module()
    entry = _entry(options={"confirmed_regulated_tariffs": ["fixture"]})
    hass = Hass(entry)
    connection = Connection()
    module.async_register_manual_customer_tariff_websocket(hass)

    asyncio.run(registered[0](hass, connection, _propose_message()))

    assert connection.errors == []
    names = [item[0] for item in calls]
    assert names.index("regulated") < names.index("entry_registry")
    assert names.index("entry_registry") < names.index("discover") < names.index("fetch")
    assert names.index("fetch") < names.index("manual_preview") < names.index("stage")
    assert "parse" not in names
    discover = next(item for item in calls if item[0] == "discover")
    assert discover[4].postcode == "41201"
    stage = next(item for item in calls if item[0] == "stage")
    assert stage[2]["contract"].customer_confirmed is False
    assert stage[2]["authority_method"].value == "manual_user_entry"
    manual_input = next(item for item in calls if item[0] == "manual_preview")[1][
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
    assert "postcode" not in payload
    assert "source_context" not in payload


def test_manual_commercial_payload_is_strict_decimal_string_only_and_fails_before_regulator() -> None:
    for invalid_manual in (
        {
            "high_rate_czk_per_kwh": "2.899e0",
            "low_rate_czk_per_kwh": "2.899",
            "supplier_standing_czk_month": "168",
        },
        {
            "high_rate_czk_per_kwh": 2.899,
            "low_rate_czk_per_kwh": "2.899",
            "supplier_standing_czk_month": "168",
        },
        {
            "high_rate_czk_per_kwh": "2.899",
            "low_rate_czk_per_kwh": "2.899",
            "supplier_standing_czk_month": "168",
            "authority_method": "verified_parser",
        },
    ):
        module, registered, _schemas, calls, _proposal = load_module()
        hass = Hass(_entry())
        connection = Connection()
        module.async_register_manual_customer_tariff_websocket(hass)
        message = _propose_message()
        message["manual_commercial"] = invalid_manual
        asyncio.run(registered[0](hass, connection, message))
        assert connection.errors[0][1] == "invalid_manual_commercial"
        assert not any(
            item[0] in {"regulated", "discover", "fetch", "stage"}
            for item in calls
        )


def test_missing_regulator_and_invalid_source_context_fail_before_supplier_network() -> None:
    module, registered, _schemas, calls, _proposal = load_module(
        regulator_mode="missing"
    )
    hass = Hass(_entry())
    connection = Connection()
    module.async_register_manual_customer_tariff_websocket(hass)
    asyncio.run(registered[0](hass, connection, _propose_message()))
    assert connection.errors[0][1] == "regulated_tariff_not_available"
    assert not any(
        item[0] in {"entry_registry", "discover", "fetch", "stage"}
        for item in calls
    )

    module, registered, _schemas, calls, _proposal = load_module()
    hass = Hass(_entry())
    connection = Connection()
    module.async_register_manual_customer_tariff_websocket(hass)
    asyncio.run(
        registered[0](
            hass,
            connection,
            _propose_message(source_context={"postcode": "99999"}),
        )
    )
    assert connection.errors[0][1] == "invalid_source_context"
    assert not any(
        item[0] in {"regulated", "discover", "fetch", "stage"}
        for item in calls
    )


def test_candidate_and_download_failures_never_stage_manual_prices() -> None:
    module, registered, _schemas, calls, _proposal = load_module()
    hass = Hass(_entry())
    connection = Connection()
    module.async_register_manual_customer_tariff_websocket(hass)
    asyncio.run(
        registered[0](
            hass,
            connection,
            _propose_message(candidate_fingerprint="0" * 64),
        )
    )
    assert connection.errors[0][1] == "candidate_not_found"
    assert not any(
        item[0] in {"fetch", "manual_preview", "stage"} for item in calls
    )

    module, registered, _schemas, calls, _proposal = load_module(
        download_mode="not_modified"
    )
    hass = Hass(_entry())
    connection = Connection()
    module.async_register_manual_customer_tariff_websocket(hass)
    asyncio.run(registered[0](hass, connection, _propose_message()))
    assert connection.errors[0][1] == "not_modified_without_cached_document"
    assert not any(item[0] in {"manual_preview", "stage"} for item in calls)


def test_unsafe_manual_preview_cannot_be_staged() -> None:
    module, registered, _schemas, calls, _proposal = load_module(unsafe_preview=True)
    hass = Hass(_entry())
    connection = Connection()
    module.async_register_manual_customer_tariff_websocket(hass)
    asyncio.run(registered[0](hass, connection, _propose_message()))
    assert connection.errors[0][1] == "manual_tariff_proposal_failed"
    assert not any(item[0] == "stage" for item in calls)


def test_manual_confirm_requires_explicit_manual_authority_before_generic_confirmation() -> None:
    module, registered, _schemas, calls, Proposal = load_module()
    proposal = Proposal()
    entry = _entry(options={"stored_proposal": proposal, "authority_mode": "manual"})
    hass = Hass(entry)
    connection = Connection()
    module.async_register_manual_customer_tariff_websocket(hass)

    asyncio.run(registered[1](hass, connection, _confirm_message()))

    assert connection.errors == []
    names = [item[0] for item in calls]
    first_authority = names.index("authority")
    confirm = names.index("confirm")
    second_authority = names.index("authority", first_authority + 1)
    assert first_authority < confirm < second_authority
    assert len(hass.config_entries.updates) == 1
    payload = connection.results[0][1]
    assert payload["confirmed"] is True
    assert payload["authority_method"] == "manual_user_entry"
    assert payload["manual_entry"] is True
    assert payload["parsing_performed"] is False
    assert payload["activation_performed"] is True


def test_manual_confirm_rejects_verified_or_legacy_authority_before_confirmation() -> None:
    module, registered, _schemas, calls, Proposal = load_module(
        authority_mode="verified"
    )
    entry = _entry(
        options={"stored_proposal": Proposal(), "authority_mode": "verified"}
    )
    hass = Hass(entry)
    connection = Connection()
    module.async_register_manual_customer_tariff_websocket(hass)
    asyncio.run(registered[1](hass, connection, _confirm_message()))
    assert connection.errors[0][1] == "manual_tariff_authority_mismatch"
    assert not any(item[0] == "confirm" for item in calls)

    module, registered, _schemas, calls, Proposal = load_module(
        authority_mode="missing"
    )
    entry = _entry(
        options={"stored_proposal": Proposal(), "authority_mode": "missing"}
    )
    hass = Hass(entry)
    connection = Connection()
    module.async_register_manual_customer_tariff_websocket(hass)
    asyncio.run(registered[1](hass, connection, _confirm_message()))
    assert connection.errors[0][1] == "manual_tariff_authority_not_found"
    assert not any(item[0] == "confirm" for item in calls)


def test_manual_confirm_is_idempotent_and_malformed_fingerprint_fails_closed() -> None:
    module, registered, _schemas, _calls, Proposal = load_module(
        already_confirmed=True
    )
    entry = _entry(
        options={
            "stored_proposal": Proposal(),
            "authority_mode": "manual",
            "confirmed": "a" * 64,
        }
    )
    hass = Hass(entry)
    connection = Connection()
    module.async_register_manual_customer_tariff_websocket(hass)
    asyncio.run(registered[1](hass, connection, _confirm_message()))
    assert connection.errors == []
    assert hass.config_entries.updates == []
    payload = connection.results[0][1]
    assert payload["confirmed"] is True
    assert payload["confirmation_performed"] is False
    assert payload["activation_performed"] is False

    module, registered, _schemas, calls, Proposal = load_module()
    entry = _entry(
        options={"stored_proposal": Proposal(), "authority_mode": "manual"}
    )
    hass = Hass(entry)
    connection = Connection()
    module.async_register_manual_customer_tariff_websocket(hass)
    asyncio.run(registered[1](hass, connection, _confirm_message("bad")))
    assert connection.errors[0][1] == "invalid_proposal_fingerprint"
    assert not any(item[0] in {"authority", "confirm"} for item in calls)
