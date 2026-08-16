import asyncio
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import types


FIXED_NOW = datetime(2026, 8, 14, 17, 0, tzinfo=timezone.utc)


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_module(*, confirm_mode="success", propose_mode="success", official_mode="success"):
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.const",
        "custom_components.frakon_energy.cz_regulated_2026_catalog",
        "custom_components.frakon_energy.regulated_proposals",
        "custom_components.frakon_energy.regulated_proposals_ws_api",
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

    calls = []

    class Inputs:
        def __init__(self, distributor, tariff, breaker, day):
            self.distributor = distributor
            self.tariff = tariff
            self.breaker = breaker
            self.day = day

        def to_bundle(self, *, confirmed=False):
            assert confirmed is False
            return {
                "distributor": self.distributor,
                "distribution_tariff": self.tariff,
                "breaker_code": self.breaker,
                "confirmed": False,
            }

        def regulated_evidence(self):
            return ({"scope": "regulated", "source": "official"},)

    official = types.ModuleType("custom_components.frakon_energy.cz_regulated_2026_catalog")

    def official_2026_regulated_inputs(*, distributor, distribution_tariff, breaker_code, day):
        calls.append(("official_inputs", distributor, distribution_tariff, breaker_code, day))
        if official_mode == "missing":
            raise LookupError("official frozen regulated catalog does not yet cover this distribution tariff")
        if official_mode == "invalid":
            raise ValueError("invalid official regulated identity")
        return Inputs(distributor, distribution_tariff, breaker_code, day)

    official.official_2026_regulated_inputs = official_2026_regulated_inputs
    sys.modules[official.__name__] = official

    proposals = types.ModuleType("custom_components.frakon_energy.regulated_proposals")

    class Proposal:
        fingerprint = "a" * 64

        def __init__(self, bundle=None, evidence=(), proposed_at=FIXED_NOW):
            self.bundle = {"confirmed": False} if bundle is None else bundle
            self.evidence = tuple(evidence)
            self.proposed_at = proposed_at

        def as_dict(self):
            return {
                "schema_version": 1,
                "fingerprint": self.fingerprint,
                "proposed_at": self.proposed_at.isoformat(),
                "bundle": self.bundle,
                "evidence": list(self.evidence),
            }

    class Version:
        fingerprint = "b" * 64

    def regulated_tariff_proposal_from_payload(bundle, evidence, *, proposed_at):
        calls.append(("build", bundle, evidence, proposed_at))
        if propose_mode == "invalid":
            raise ValueError("regulated proposal bundle must remain unconfirmed")
        return Proposal(bundle=bundle, evidence=evidence, proposed_at=proposed_at)

    def append_regulated_tariff_proposal(options, proposal):
        calls.append(("append", dict(options), proposal.fingerprint))
        if options.get("proposal_saved"):
            return dict(options)
        return {**dict(options), "proposal_saved": proposal.fingerprint}

    def confirm_regulated_tariff_proposal(options, fingerprint):
        calls.append(("confirm", dict(options), fingerprint))
        if confirm_mode == "missing":
            raise LookupError("regulated tariff proposal not found")
        if confirm_mode == "invalid":
            raise ValueError("proposal fingerprint must be a lowercase SHA-256 hex digest")
        if options.get("confirmed_version"):
            return dict(options), Version()
        return {**dict(options), "confirmed_version": Version.fingerprint}, Version()

    proposals.RegulatedTariffProposal = Proposal
    proposals.regulated_tariff_proposal_from_payload = regulated_tariff_proposal_from_payload
    proposals.append_regulated_tariff_proposal = append_regulated_tariff_proposal
    proposals.confirm_regulated_tariff_proposal = confirm_regulated_tariff_proposal
    sys.modules[proposals.__name__] = proposals

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
        return lambda func: func

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
    sys.modules["homeassistant.components.websocket_api"] = websocket_api
    components.websocket_api = websocket_api

    core = types.ModuleType("homeassistant.core")
    core.callback = lambda func: func
    core.HomeAssistant = type("HomeAssistant", (), {})
    sys.modules["homeassistant.core"] = core

    util = types.ModuleType("homeassistant.util")
    util.__path__ = []
    sys.modules["homeassistant.util"] = util
    dt = types.ModuleType("homeassistant.util.dt")
    dt.now = lambda: FIXED_NOW
    sys.modules["homeassistant.util.dt"] = dt
    util.dt = dt

    ws = _load(
        "custom_components.frakon_energy.regulated_proposals_ws_api",
        "custom_components/frakon_energy/regulated_proposals_ws_api.py",
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


def test_registration_is_idempotent_and_confirmation_is_fingerprint_only() -> None:
    ws, registered, schemas, _calls = load_module()
    hass = Hass(_entry())
    ws.async_register_regulated_tariff_proposals_websocket(hass)
    ws.async_register_regulated_tariff_proposals_websocket(hass)

    assert len(registered) == 3
    assert len(schemas) == 3
    assert set(schemas[0]) == {"type", "entry_id", "bundle", "evidence"}
    assert set(schemas[1]) == {
        "type", "entry_id", "distributor", "distribution_tariff", "breaker_code", "day"
    }
    assert set(schemas[2]) == {"type", "entry_id", "proposal_fingerprint"}
    for forbidden in ("bundle", "evidence", "price", "source_url"):
        assert forbidden not in schemas[2]


def test_manual_propose_uses_server_time_and_never_activates() -> None:
    ws, registered, _schemas, calls = load_module()
    entry = _entry()
    hass = Hass(entry)
    connection = Connection()
    ws.async_register_regulated_tariff_proposals_websocket(hass)
    message = {
        "id": 1,
        "type": ws.COMMAND_REGULATED_TARIFF_PROPOSE,
        "entry_id": "entry-1",
        "bundle": {"confirmed": False},
        "evidence": [{"scope": "regulated"}],
    }
    asyncio.run(registered[0](hass, connection, message))

    assert connection.admin_calls == 1
    assert connection.errors == []
    assert calls[0] == ("build", message["bundle"], message["evidence"], FIXED_NOW)
    assert len(hass.config_entries.updates) == 1
    payload = connection.results[0][1]
    assert payload["proposal_fingerprint"] == "a" * 64
    assert payload["persistence_performed"] is True
    assert payload["confirmation_performed"] is False
    assert payload["activation_performed"] is False


def test_official_propose_accepts_identity_only_and_server_authors_all_prices_and_sources() -> None:
    ws, registered, schemas, calls = load_module()
    entry = _entry()
    hass = Hass(entry)
    connection = Connection()
    ws.async_register_regulated_tariff_proposals_websocket(hass)
    message = {
        "id": 2,
        "type": ws.COMMAND_REGULATED_TARIFF_OFFICIAL_PROPOSE,
        "entry_id": "entry-1",
        "distributor": "cez_distribuce",
        "distribution_tariff": "D25d",
        "breaker_code": "3x25A",
        "day": "2026-08-16",
    }
    asyncio.run(registered[1](hass, connection, message))

    assert connection.errors == []
    assert set(schemas[1]) == set(message) - {"id"}
    assert "bundle" not in schemas[1] and "evidence" not in schemas[1]
    assert calls[0][0:4] == ("official_inputs", "cez_distribuce", "D25d", "3x25A")
    payload = connection.results[0][1]
    assert payload["server_authored"] is True
    assert payload["source_authority"] == "official_2026_frozen"
    assert payload["persistence_performed"] is True
    assert payload["confirmation_performed"] is False
    assert payload["activation_performed"] is False
    assert payload["proposal"]["bundle"]["confirmed"] is False


def test_official_propose_fails_closed_for_unsupported_identity_without_write() -> None:
    ws, registered, _schemas, _calls = load_module(official_mode="missing")
    hass = Hass(_entry())
    connection = Connection()
    ws.async_register_regulated_tariff_proposals_websocket(hass)
    asyncio.run(registered[1](hass, connection, {
        "id": 3,
        "type": ws.COMMAND_REGULATED_TARIFF_OFFICIAL_PROPOSE,
        "entry_id": "entry-1",
        "distributor": "cez_distribuce",
        "distribution_tariff": "D57d",
        "breaker_code": "3x25A",
        "day": "2026-08-16",
    }))

    assert connection.errors[0][1] == "official_regulated_tariff_not_available"
    assert hass.config_entries.updates == []


def test_repeated_propose_and_confirm_do_not_churn_options() -> None:
    ws, registered, _schemas, _calls = load_module()
    entry = _entry(options={"proposal_saved": "a" * 64, "confirmed_version": "b" * 64})
    hass = Hass(entry)
    connection = Connection()
    ws.async_register_regulated_tariff_proposals_websocket(hass)
    asyncio.run(registered[0](hass, connection, {
        "id": 4, "type": ws.COMMAND_REGULATED_TARIFF_PROPOSE, "entry_id": "entry-1",
        "bundle": {"confirmed": False}, "evidence": [{"scope": "regulated"}],
    }))
    asyncio.run(registered[2](hass, connection, {
        "id": 5, "type": ws.COMMAND_REGULATED_TARIFF_CONFIRM, "entry_id": "entry-1",
        "proposal_fingerprint": "a" * 64,
    }))

    assert hass.config_entries.updates == []
    assert connection.results[0][1]["persistence_performed"] is False
    assert connection.results[1][1]["persistence_performed"] is False
    assert connection.results[1][1]["confirmation_performed"] is False
    assert connection.results[1][1]["confirmed"] is True


def test_confirmation_updates_only_from_stored_fingerprint_and_errors_do_not_write() -> None:
    ws, registered, schemas, calls = load_module()
    entry = _entry(options={"proposal_saved": "a" * 64})
    hass = Hass(entry)
    connection = Connection()
    ws.async_register_regulated_tariff_proposals_websocket(hass)
    message = {
        "id": 6,
        "type": ws.COMMAND_REGULATED_TARIFF_CONFIRM,
        "entry_id": "entry-1",
        "proposal_fingerprint": "a" * 64,
    }
    asyncio.run(registered[2](hass, connection, message))
    assert set(schemas[2]) == {"type", "entry_id", "proposal_fingerprint"}
    assert calls[-1] == ("confirm", {"proposal_saved": "a" * 64}, "a" * 64)
    assert connection.results[0][1]["regulated_version_fingerprint"] == "b" * 64
    assert connection.results[0][1]["activation_performed"] is False

    for mode, expected_code in (("missing", "regulated_proposal_not_found"), ("invalid", "regulated_confirmation_failed")):
        ws2, registered2, _schemas2, _calls2 = load_module(confirm_mode=mode)
        hass2 = Hass(_entry(options={"proposal_saved": "a" * 64}))
        connection2 = Connection()
        ws2.async_register_regulated_tariff_proposals_websocket(hass2)
        asyncio.run(registered2[2](hass2, connection2, message))
        assert connection2.errors[0][1] == expected_code
        assert hass2.config_entries.updates == []
