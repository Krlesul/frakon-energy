from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from custom_components.frakon_energy import load_execution_policy_ws_api as policy_ws
from custom_components.frakon_energy.const import DOMAIN
from custom_components.frakon_energy.load_execution_policy import (
    DECISION_APPROVAL_REQUIRED,
    DECISION_BLOCKED,
    EXECUTION_MODE_APPROVAL_REQUIRED,
    REASON_ENTITY_UNAVAILABLE,
    REASON_PLAN_UNAVAILABLE,
    REASON_POLICY_DISABLED,
    LoadExecutionPolicy,
    upsert_execution_policy,
)
from custom_components.frakon_energy.load_profiles import (
    PROFILE_KIND_EV,
    LoadProfile,
    upsert_profile,
)


class _FakeEntry:
    domain = DOMAIN
    entry_id = "entry-1"

    def __init__(self, options: dict[str, object]) -> None:
        self.options = options


class _FakeConfigEntries:
    def __init__(self, entry: _FakeEntry) -> None:
        self.entry = entry

    def async_get_entry(self, entry_id: str) -> _FakeEntry | None:
        return self.entry if entry_id == self.entry.entry_id else None


class _FakeStates:
    def __init__(self, states: dict[str, str]) -> None:
        self._states = states

    def get(self, entity_id: str) -> object | None:
        state = self._states.get(entity_id)
        return SimpleNamespace(state=state) if state is not None else None


class _FakeHass:
    def __init__(self, options: dict[str, object], states: dict[str, str]) -> None:
        self.config_entries = _FakeConfigEntries(_FakeEntry(options))
        self.states = _FakeStates(states)


def _options(*, with_policy: bool) -> dict[str, object]:
    profile = LoadProfile(
        "ev-home",
        "Enyaq",
        PROFILE_KIND_EV,
        120,
        11.0,
        entity_id="switch.enyaq_charging",
    )
    options: dict[str, object] = upsert_profile({}, profile)
    if with_policy:
        options = upsert_execution_policy(
            options,
            LoadExecutionPolicy(
                "ev-home",
                mode=EXECUTION_MODE_APPROVAL_REQUIRED,
                max_power_kw=11.0,
                max_duration_minutes=120,
            ),
        )
    return options


def _preview() -> dict[str, object]:
    return {
        "load_id": "ev-home",
        "name": "Enyaq",
        "starts_at": "2026-08-08T01:00:00+02:00",
        "ends_at": "2026-08-08T03:00:00+02:00",
        "duration_minutes": 120,
        "interval_count": 8,
        "power_kw": 11.0,
        "average_czk_kwh": 2.0,
        "minimum_czk_kwh": 1.5,
        "maximum_czk_kwh": 2.5,
        "estimated_energy_kwh": 22.0,
        "estimated_cost_czk": 44.0,
        "read_only": True,
    }


@pytest.mark.asyncio
async def test_missing_policy_evaluates_as_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_preview(*args: object, **kwargs: object) -> dict[str, object]:
        return _preview()

    monkeypatch.setattr(policy_ws, "async_preview_load_plan", fake_preview)
    hass = _FakeHass(_options(with_policy=False), {"switch.enyaq_charging": "off"})

    result = await policy_ws.async_evaluate_profile_execution(
        hass, entry_id="entry-1", profile_id="ev-home"
    )

    assert result["status"] == DECISION_BLOCKED
    assert REASON_POLICY_DISABLED in result["reasons"]
    assert result["execution_performed"] is False
    assert result["executor_available"] is False


@pytest.mark.asyncio
async def test_valid_policy_can_only_require_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_preview(*args: object, **kwargs: object) -> dict[str, object]:
        return _preview()

    monkeypatch.setattr(policy_ws, "async_preview_load_plan", fake_preview)
    hass = _FakeHass(_options(with_policy=True), {"switch.enyaq_charging": "off"})

    result = await policy_ws.async_evaluate_profile_execution(
        hass,
        entry_id="entry-1",
        profile_id="ev-home",
        now=datetime(2026, 8, 7, 18, 0, tzinfo=timezone(timedelta(hours=2))),
    )

    assert result["status"] == DECISION_APPROVAL_REQUIRED
    assert result["reasons"] == []
    assert result["entity_available"] is True
    assert result["execution_performed"] is False
    assert result["executor_available"] is False


@pytest.mark.asyncio
async def test_unavailable_entity_blocks_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_preview(*args: object, **kwargs: object) -> dict[str, object]:
        return _preview()

    monkeypatch.setattr(policy_ws, "async_preview_load_plan", fake_preview)
    hass = _FakeHass(_options(with_policy=True), {"switch.enyaq_charging": "unavailable"})

    result = await policy_ws.async_evaluate_profile_execution(
        hass, entry_id="entry-1", profile_id="ev-home"
    )

    assert result["status"] == DECISION_BLOCKED
    assert REASON_ENTITY_UNAVAILABLE in result["reasons"]
    assert result["execution_performed"] is False


@pytest.mark.asyncio
async def test_missing_plan_is_blocked_without_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_preview(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(policy_ws, "async_preview_load_plan", fake_preview)
    hass = _FakeHass(_options(with_policy=True), {"switch.enyaq_charging": "off"})

    result = await policy_ws.async_evaluate_profile_execution(
        hass, entry_id="entry-1", profile_id="ev-home"
    )

    assert result["status"] == DECISION_BLOCKED
    assert REASON_PLAN_UNAVAILABLE in result["reasons"]
    assert result["plan"] is None
    assert result["execution_performed"] is False
    assert result["executor_available"] is False
