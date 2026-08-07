from types import SimpleNamespace

import pytest

from custom_components.frakon_energy import load_execution_policy_ws_api
from custom_components.frakon_energy.load_execution_policy import (
    EXECUTION_MODE_APPROVAL_REQUIRED,
    EXECUTION_MODE_DISABLED,
    DECISION_APPROVAL_REQUIRED,
    DECISION_BLOCKED,
    REASON_ENTITY_UNAVAILABLE,
    REASON_POLICY_DISABLED,
    REASON_PROFILE_DISABLED,
    LoadExecutionPolicy,
)
from custom_components.frakon_energy.load_execution_policy_options import upsert_policy
from custom_components.frakon_energy.load_profiles import (
    PROFILE_KIND_EV,
    LoadProfile,
    upsert_profile,
)


class FakeConfigEntries:
    def __init__(self, entry: object) -> None:
        self.entry = entry

    def async_get_entry(self, entry_id: str) -> object | None:
        return self.entry if entry_id == self.entry.entry_id else None


class FakeStates:
    def __init__(self, values: dict[str, object]) -> None:
        self.values = values

    def get(self, entity_id: str | None) -> object | None:
        return None if entity_id is None else self.values.get(entity_id)


class FakeHass:
    def __init__(self, entry: object, states: dict[str, object]) -> None:
        self.config_entries = FakeConfigEntries(entry)
        self.states = FakeStates(states)


def _plan(profile: LoadProfile) -> dict[str, object]:
    return {
        "load_id": profile.profile_id,
        "name": profile.name,
        "starts_at": "2026-08-07T20:00:00+02:00",
        "ends_at": "2026-08-07T22:00:00+02:00",
        "duration_minutes": profile.duration_minutes,
        "interval_count": profile.duration_minutes // 15,
        "power_kw": profile.power_kw,
        "average_czk_kwh": 2.0,
        "minimum_czk_kwh": 1.8,
        "maximum_czk_kwh": 2.2,
        "estimated_energy_kwh": profile.power_kw * profile.duration_minutes / 60,
        "estimated_cost_czk": profile.power_kw * profile.duration_minutes / 60 * 2.0,
        "read_only": True,
    }


def _patch_preview(monkeypatch: pytest.MonkeyPatch, profile: LoadProfile, value: dict[str, object] | None) -> None:
    async def fake_preview(*args: object, **kwargs: object) -> dict[str, object] | None:
        return value

    monkeypatch.setattr(load_execution_policy_ws_api, "async_preview_load_plan", fake_preview)


@pytest.mark.asyncio
async def test_evaluate_returns_approval_required_for_available_bound_entity(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = LoadProfile("ev", "Enyaq", PROFILE_KIND_EV, 120, 11.0, True, "switch.ev_charging")
    options = upsert_profile({}, profile)
    options = upsert_policy(options, LoadExecutionPolicy("ev", EXECUTION_MODE_APPROVAL_REQUIRED, 11.0, 120))
    entry = SimpleNamespace(entry_id="entry", domain="frakon_energy", options=options)
    hass = FakeHass(entry, {"switch.ev_charging": SimpleNamespace(state="off")})
    _patch_preview(monkeypatch, profile, _plan(profile))

    result = await load_execution_policy_ws_api.async_evaluate_profile_policy(hass, entry_id="entry", profile_id="ev")

    assert result["available"] is True
    assert result["entity_available"] is True
    assert result["entity_state"] == "off"
    assert result["decision"]["status"] == DECISION_APPROVAL_REQUIRED
    assert result["decision"]["reasons"] == []
    assert result["execution_performed"] is False
    assert result["automatic_execution_supported"] is False


@pytest.mark.asyncio
async def test_evaluate_blocks_unavailable_entity(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = LoadProfile("ev", "Enyaq", PROFILE_KIND_EV, 120, 11.0, True, "switch.ev_charging")
    options = upsert_profile({}, profile)
    options = upsert_policy(options, LoadExecutionPolicy("ev", EXECUTION_MODE_APPROVAL_REQUIRED, 11.0, 120))
    entry = SimpleNamespace(entry_id="entry", domain="frakon_energy", options=options)
    hass = FakeHass(entry, {"switch.ev_charging": SimpleNamespace(state="unavailable")})
    _patch_preview(monkeypatch, profile, _plan(profile))

    result = await load_execution_policy_ws_api.async_evaluate_profile_policy(hass, entry_id="entry", profile_id="ev")

    assert result["decision"]["status"] == DECISION_BLOCKED
    assert REASON_ENTITY_UNAVAILABLE in result["decision"]["reasons"]
    assert result["entity_available"] is False


@pytest.mark.asyncio
async def test_evaluate_missing_policy_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = LoadProfile("ev", "Enyaq", PROFILE_KIND_EV, 120, 11.0, True, "switch.ev_charging")
    options = upsert_profile({}, profile)
    entry = SimpleNamespace(entry_id="entry", domain="frakon_energy", options=options)
    hass = FakeHass(entry, {"switch.ev_charging": SimpleNamespace(state="off")})
    _patch_preview(monkeypatch, profile, _plan(profile))

    result = await load_execution_policy_ws_api.async_evaluate_profile_policy(hass, entry_id="entry", profile_id="ev")

    assert result["policy"]["mode"] == EXECUTION_MODE_DISABLED
    assert result["decision"]["status"] == DECISION_BLOCKED
    assert REASON_POLICY_DISABLED in result["decision"]["reasons"]


@pytest.mark.asyncio
async def test_evaluate_disabled_profile_returns_blocked_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = LoadProfile("ev", "Enyaq", PROFILE_KIND_EV, 120, 11.0, False, "switch.ev_charging")
    options = upsert_profile({}, profile)
    options = upsert_policy(options, LoadExecutionPolicy("ev", EXECUTION_MODE_APPROVAL_REQUIRED, 11.0, 120))
    entry = SimpleNamespace(entry_id="entry", domain="frakon_energy", options=options)
    hass = FakeHass(entry, {"switch.ev_charging": SimpleNamespace(state="off")})
    _patch_preview(monkeypatch, profile, _plan(profile))

    result = await load_execution_policy_ws_api.async_evaluate_profile_policy(hass, entry_id="entry", profile_id="ev")

    assert result["decision"]["status"] == DECISION_BLOCKED
    assert REASON_PROFILE_DISABLED in result["decision"]["reasons"]


@pytest.mark.asyncio
async def test_evaluate_no_plan_stays_read_only_and_returns_no_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = LoadProfile("ev", "Enyaq", PROFILE_KIND_EV, 120, 11.0, True, "switch.ev_charging")
    options = upsert_profile({}, profile)
    entry = SimpleNamespace(entry_id="entry", domain="frakon_energy", options=options)
    hass = FakeHass(entry, {"switch.ev_charging": SimpleNamespace(state="off")})
    _patch_preview(monkeypatch, profile, None)

    result = await load_execution_policy_ws_api.async_evaluate_profile_policy(hass, entry_id="entry", profile_id="ev")

    assert result["available"] is False
    assert result["decision"] is None
    assert result["execution_performed"] is False
    assert result["read_only"] is True
