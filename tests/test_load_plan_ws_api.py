from datetime import datetime, timedelta, timezone

import pytest

from custom_components.frakon_energy import load_plan_ws_api
from custom_components.frakon_energy.const import DOMAIN
from custom_components.frakon_energy.load_profiles import PROFILE_KIND_EV, LoadProfile, upsert_profile


def _interval(start: datetime, price: float) -> dict[str, object]:
    end = start + timedelta(minutes=15)
    return {
        "starts_at": start.isoformat(),
        "ends_at": end.isoformat(),
        "price_czk_kwh": price,
    }


def test_parse_datetime_requires_timezone() -> None:
    with pytest.raises(ValueError, match="timezone offset"):
        load_plan_ws_api._parse_datetime("2026-08-07T18:00:00", "earliest_start")


def test_available_intervals_combines_today_and_tomorrow() -> None:
    today = {"starts_at": "today"}
    tomorrow = {"starts_at": "tomorrow"}
    payload = {
        "today": {"intervals": [today]},
        "tomorrow": {"intervals": [tomorrow]},
    }
    assert load_plan_ws_api._available_intervals(payload) == [today, tomorrow]


@pytest.mark.asyncio
async def test_preview_defaults_to_now_and_never_selects_past(monkeypatch: pytest.MonkeyPatch) -> None:
    tz = timezone(timedelta(hours=2))
    day_start = datetime(2026, 8, 7, 0, 0, tzinfo=tz)
    now = datetime(2026, 8, 7, 17, 41, tzinfo=tz)
    intervals = [_interval(day_start + timedelta(minutes=15 * index), 8.0) for index in range(96)]

    # The globally cheapest hour is already in the past and must never be selected.
    for index in range(4, 8):
        intervals[index] = _interval(day_start + timedelta(minutes=15 * index), 0.5)

    # The cheapest eligible future hour starts at 18:00.
    for index in range(72, 76):
        intervals[index] = _interval(day_start + timedelta(minutes=15 * index), 2.0)

    async def fake_payload(hass: object, *, now: datetime | None = None) -> dict[str, object]:
        return {
            "today": {"intervals": intervals},
            "tomorrow": {"intervals": []},
            "provider": "ote",
            "exchange_rate": {"rate": 24.5, "source": "cnb"},
            "stale": False,
            "fallback_used": False,
        }

    monkeypatch.setattr(load_plan_ws_api, "async_customer_spot_payload", fake_payload)

    plan = await load_plan_ws_api.async_preview_load_plan(
        object(),
        load_id="ev",
        name="EV",
        duration_minutes=60,
        power_kw=11.0,
        now=now,
    )

    assert plan is not None
    assert plan["starts_at"] == "2026-08-07T18:00:00+02:00"
    assert plan["ends_at"] == "2026-08-07T19:00:00+02:00"
    assert plan["estimated_energy_kwh"] == pytest.approx(11.0)
    assert plan["estimated_cost_czk"] == pytest.approx(22.0)
    assert plan["read_only"] is True
    assert plan["price_source"] == "ote"
    assert plan["spot_data_stale"] is False


@pytest.mark.asyncio
async def test_preview_respects_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    tz = timezone(timedelta(hours=2))
    start = datetime(2026, 8, 7, 18, 0, tzinfo=tz)
    intervals = [_interval(start + timedelta(minutes=15 * index), 2.0) for index in range(8)]

    async def fake_payload(hass: object, *, now: datetime | None = None) -> dict[str, object]:
        return {
            "today": {"intervals": intervals},
            "tomorrow": {"intervals": []},
            "provider": "ote",
        }

    monkeypatch.setattr(load_plan_ws_api, "async_customer_spot_payload", fake_payload)

    plan = await load_plan_ws_api.async_preview_load_plan(
        object(),
        load_id="boiler",
        name="Boiler",
        duration_minutes=60,
        power_kw=2.0,
        earliest_start=start,
        deadline=start + timedelta(minutes=45),
        now=start,
    )

    assert plan is None


class _FakeEntry:
    domain = DOMAIN

    def __init__(self, options: dict[str, object]) -> None:
        self.options = options


class _FakeConfigEntries:
    def __init__(self, entry: _FakeEntry) -> None:
        self.entry = entry

    def async_get_entry(self, entry_id: str) -> _FakeEntry | None:
        return self.entry if entry_id == "entry-1" else None


class _FakeHass:
    def __init__(self, entry: _FakeEntry) -> None:
        self.config_entries = _FakeConfigEntries(entry)


@pytest.mark.asyncio
async def test_preview_profile_uses_persisted_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = LoadProfile("ev-home", "Enyaq", PROFILE_KIND_EV, 120, 11.0)
    entry = _FakeEntry(upsert_profile({}, profile))
    hass = _FakeHass(entry)
    captured: dict[str, object] = {}

    async def fake_preview(hass_arg: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"starts_at": "2026-08-07T22:00:00+02:00", "read_only": True}

    monkeypatch.setattr(load_plan_ws_api, "async_preview_load_plan", fake_preview)

    resolved, plan = await load_plan_ws_api.async_preview_profile_plan(
        hass, entry_id="entry-1", profile_id="ev-home"
    )

    assert resolved == profile
    assert plan is not None
    assert captured["load_id"] == "ev-home"
    assert captured["name"] == "Enyaq"
    assert captured["duration_minutes"] == 120
    assert captured["power_kw"] == 11.0


@pytest.mark.asyncio
async def test_preview_profile_rejects_disabled_profile() -> None:
    profile = LoadProfile("ev-home", "Enyaq", PROFILE_KIND_EV, 120, 11.0, enabled=False)
    hass = _FakeHass(_FakeEntry(upsert_profile({}, profile)))

    with pytest.raises(ValueError, match="disabled"):
        await load_plan_ws_api.async_preview_profile_plan(
            hass, entry_id="entry-1", profile_id="ev-home"
        )
