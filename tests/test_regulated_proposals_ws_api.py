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


def load_module(*, confirm_mode="success", propose_mode="success"):
    names = (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.const",
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
    proposals = types.ModuleType("custom_components.frakon_energy.regulated_proposals")

    class Proposal:
        fingerprint = "a" * 64
        proposed_at = FIXED_NOW

        def as_dict(self):
            return {
                "schema_version": 1,
                "fingerprint": self.fingerprint,
                "proposed_at": self.proposed_at.isoformat(),
                "bundle": {"confirmed": False},
                "evidence": [{"scope": "regulated"}],
            }

    class Version:
        fingerprint = "b" * 64

    def regulated_tariff_proposal_from_payload(bundle, evidence, *, proposed_at):
        calls.append(("build", bundle, evidence, proposed_at))
        if propose_mode == "invalid":
            raise ValueError("regulated proposal bundle must remain unconfirmed")
        return Proposal()

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


def test_registration_is_idempotent_and_confirm_schema_accepts_fingerprint_only() -> None:
    ws, registered, schemas, _calls = load_module()
    hass = Hass(_entry())

    ws.async_register_regulated_tariff_proposals_websocket(hass)
    ws.async_register_regulated_tariff_proposals_websocket(hass)

    assert len(registered) == 2
    assert len(schemas) == 2
    assert set(schemas[0]) == {"type", "entry_id", "bundle", "evidence"}
    assert set(schemas[1]) == {"type", "entry_id", "proposal_fingerprint"}
    assert "bundle" not in schemas[1]
    assert "evidence" not in schemas[1]


def test_propose_uses_server_time_persists_only_unconfirmed_proposal_and_never_activates() -> None:
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
    assert payload["proposal"]["bundle"]["confirmed"] is False


def test_repeated_propose_and_repeated_confirm_do_not_churn_options() -> None:
    ws, registered, _schemas, _calls = load_module()
    entry = _entry(options={"proposal_saved": "a" * 64, "confirmed_version": "b" * 64})
    hass = Hass(entry)
    connection = Connection()
    ws.async_register_regulated_tariff_proposals_websocket(hass)

    propose = {
        "id": 2,
        "type": ws.COMMAND_REGULATED_TARIFF_PROPOSE,
        "entry_id": "entry-1",
        "bundle": {"confirmed": False},
        "evidence": [{"scope": "regulated"}],
    }
    confirm = {
        "id": 3,
        "type": ws.COMMAND_REGULATED_TARIFF_CONFIRM,
        "entry_id": "entry-1",
        "proposal_fingerprint": "a" * 64,
    }
    asyncio.run(registered[0](hass, connection, propose))
    asyncio.run(registered[1](hass, connection, confirm))

    assert hass.config_entries.updates == []
    assert connection.results[0][1]["persistence_performed"] is False
    assert connection.results[1][1]["persistence_performed"] is False
    assert connection.results[1][1]["confirmation_performed"] is False
    assert connection.results[1][1]["confirmed"] is True
    assert connection.results[1][1]["activation_performed"] is False


def test_confirmation_updates_only_from_stored_fingerprint_and_never_accepts_price_payload() -> None:
    ws, registered, schemas, calls = load_module()
    entry = _entry(options={"proposal_saved": "a" * 64})
    hass = Hass(entry)
    connection = Connection()
    ws.async_register_regulated_tariff_proposals_websocket(hass)

    message = {
        "id": 4,
        "type": ws.COMMAND_REGULATED_TARIFF_CONFIRM,
        "entry_id": "entry-1",
        "proposal_fingerprint": "a" * 64,
    }
    asyncio.run(registered[1](hass, connection, message))

    assert set(schemas[1]) == {"type", "entry_id", "proposal_fingerprint"}
    assert calls[-1] == ("confirm", {"proposal_saved": "a" * 64}, "a" * 64)
    assert len(hass.config_entries.updates) == 1
    payload = connection.results[0][1]
    assert payload["regulated_version_fingerprint"] == "b" * 64
    assert payload["confirmed"] is True
    assert payload["confirmation_performed"] is True
    assert payload["activation_performed"] is False


def test_invalid_or_unknown_confirmation_and_wrong_entry_fail_without_write() -> None:
    for mode, expected_code in (
        ("missing", "regulated_proposal_not_found"),
        ("invalid", "regulated_confirmation_failed"),
    ):
        ws, registered, _schemas, _calls = load_module(confirm_mode=mode)
        hass = Hass(_entry(options={"proposal_saved": "a" * 64}))
        connection = Connection()
        ws.async_register_regulated_tariff_proposals_websocket(hass)
        asyncio.run(
            registered[1](
                hass,
                connection,
                {
                    "id": 5,
                    "type": ws.COMMAND_REGULATED_TARIFF_CONFIRM,
                    "entry_id": "entry-1",
                    "proposal_fingerprint": "a" * 64,
                },
            )
        )
        assert connection.errors[0][1] == expected_code
        assert hass.config_entries.updates == []

    ws, registered, _schemas, calls = load_module()
    hass = Hass(_entry(domain="other_domain"))
    connection = Connection()
    ws.async_register_regulated_tariff_proposals_websocket(hass)
    asyncio.run(
        registered[1](
            hass,
            connection,
            {
                "id": 6,
                "type": ws.COMMAND_REGULATED_TARIFF_CONFIRM,
                "entry_id": "entry-1",
                "proposal_fingerprint": "a" * 64,
            },
        )
    )
    assert connection.errors[0][1] == "entry_not_found"
    assert calls == []
    assert hass.config_entries.updates == []
