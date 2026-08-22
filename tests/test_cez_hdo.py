from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

from homeassistant.util import dt as dt_util

from custom_components.frakon_energy.hdo_coordinator import CezHdoCoordinator
from custom_components.frakon_energy.providers.cez_hdo import CezHdoAdapter, CezHdoSnapshot
from custom_components.frakon_energy.providers.cez_hdo_discovery import (
    CezHdoSource,
    _is_frakon_generated_schedule,
)


class FakeStates:
    def __init__(self, states):
        self._states = states
        for entity_id, state in states.items():
            state.entity_id = entity_id

    def get(self, entity_id):
        return self._states.get(entity_id)

    def async_all(self, domain=None):
        states = list(self._states.values())
        if not domain:
            return states
        prefix = f"{domain}."
        return [state for state in states if state.entity_id.startswith(prefix)]


def _state(value, **attributes):
    return SimpleNamespace(state=value, attributes=attributes)


def _source() -> CezHdoSource:
    return CezHdoSource(
        source_id="signal-1",
        name="HDO doma",
        schedule_entity_id="sensor.hdo_schedule",
        low_tariff_entity_id="binary_sensor.hdo_nt",
        current_price_entity_id="sensor.hdo_price",
        data_valid_entity_id="binary_sensor.hdo_valid",
    )


def test_adapter_returns_live_countdown_and_current_interval(monkeypatch):
    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", dt_util.get_time_zone("Europe/Prague"))
    hass = SimpleNamespace(
        states=FakeStates(
            {
                "sensor.hdo_schedule": _state(
                    "ok",
                    schedule=[
                        {
                            "start": "2026-08-04T21:35:00+02:00",
                            "end": "2026-08-04T23:50:00+02:00",
                            "tariff": "NT",
                        },
                        {
                            "start": "2026-08-04T23:50:00+02:00",
                            "end": "2026-08-05T02:00:00+02:00",
                            "tariff": "VT",
                        },
                    ],
                ),
                "binary_sensor.hdo_nt": _state("on"),
                "sensor.hdo_price": _state("4.67289"),
                "binary_sensor.hdo_valid": _state("on"),
            }
        )
    )

    snapshot = CezHdoAdapter(hass, _source()).snapshot(
        datetime.fromisoformat("2026-08-04T22:00:00+02:00")
    )

    assert snapshot.tariff == "NT"
    assert snapshot.low_tariff_active is True
    assert snapshot.interval_start == datetime.fromisoformat("2026-08-04T21:35:00+02:00")
    assert snapshot.interval_end == datetime.fromisoformat("2026-08-04T23:50:00+02:00")
    assert snapshot.next_switch == datetime.fromisoformat("2026-08-04T23:50:00+02:00")
    assert snapshot.countdown_seconds == 6600
    assert snapshot.current_price == 4.67289
    assert snapshot.data_valid is True


def test_adapter_handles_interval_crossing_midnight(monkeypatch):
    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", dt_util.get_time_zone("Europe/Prague"))
    hass = SimpleNamespace(
        states=FakeStates(
            {
                "sensor.hdo_schedule": _state(
                    "ok",
                    schedule=[
                        {
                            "start": "2026-08-04T23:50:00+02:00",
                            "end": "2026-08-05T02:00:00+02:00",
                            "tariff": "VT",
                        }
                    ],
                )
            }
        )
    )

    snapshot = CezHdoAdapter(hass, _source()).snapshot(
        datetime.fromisoformat("2026-08-05T00:30:00+02:00")
    )

    assert snapshot.tariff == "VT"
    assert snapshot.countdown_seconds == 5400
    assert snapshot.next_switch == datetime.fromisoformat("2026-08-05T02:00:00+02:00")
    assert snapshot.today_schedule == (
        {
            "start": "2026-08-05T00:00:00+02:00",
            "end": "2026-08-05T02:00:00+02:00",
            "tariff": "VT",
        },
    )


def test_adapter_recovers_exact_upstream_schedule_from_live_sibling_suffix(monkeypatch):
    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", dt_util.get_time_zone("Europe/Prague"))
    source = CezHdoSource(
        source_id="home",
        name="HDO doma",
        schedule_entity_id="sensor.frakon_energy_hdo_dnesni_rozvrh",
        low_tariff_entity_id="binary_sensor.cez_hdo_lowtariffactive_home",
        current_price_entity_id="sensor.cez_hdo_currentprice_home",
        data_valid_entity_id="binary_sensor.cez_hdo_data_valid_home",
    )
    hass = SimpleNamespace(
        states=FakeStates(
            {
                "sensor.frakon_energy_hdo_dnesni_rozvrh": _state("", schedule=[]),
                "binary_sensor.cez_hdo_lowtariffactive_home": _state("on"),
                "sensor.cez_hdo_currentprice_home": _state("4.07394"),
                "binary_sensor.cez_hdo_data_valid_home": _state("on"),
                "sensor.cez_hdo_schedule_home": _state(
                    "22.08.2026",
                    schedule=[
                        {
                            "start": "2026-08-22T00:00:00+02:00",
                            "end": "2026-08-22T08:30:00+02:00",
                            "tariff": "VT",
                        },
                        {
                            "start": "2026-08-22T08:30:00+02:00",
                            "end": "2026-08-22T10:30:00+02:00",
                            "tariff": "NT",
                        },
                        {
                            "start": "2026-08-22T10:30:00+02:00",
                            "end": "2026-08-23T00:00:00+02:00",
                            "tariff": "VT",
                        },
                    ],
                ),
                "sensor.cez_hdo_schedule_other": _state(
                    "22.08.2026",
                    schedule=[
                        {
                            "start": "2026-08-22T00:00:00+02:00",
                            "end": "2026-08-22T23:59:59+02:00",
                            "tariff": "VT",
                        }
                    ],
                ),
            }
        )
    )

    snapshot = CezHdoAdapter(hass, source).snapshot(
        datetime.fromisoformat("2026-08-22T09:52:00+02:00")
    )

    assert snapshot.tariff == "NT"
    assert snapshot.low_tariff_active is True
    assert snapshot.next_switch == datetime.fromisoformat("2026-08-22T10:30:00+02:00")
    assert snapshot.countdown_seconds == 2280
    assert len(snapshot.today_schedule) == 3
    assert snapshot.today_schedule[1]["tariff"] == "NT"


def test_adapter_keeps_live_tariff_when_schedule_is_temporarily_empty(monkeypatch):
    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", dt_util.get_time_zone("Europe/Prague"))
    hass = SimpleNamespace(
        states=FakeStates(
            {
                "sensor.hdo_schedule": _state("22.08.2026", schedule=[]),
                "binary_sensor.hdo_nt": _state("on"),
                "sensor.hdo_price": _state("4.07394"),
                "binary_sensor.hdo_valid": _state("on"),
            }
        )
    )

    snapshot = CezHdoAdapter(hass, _source()).snapshot(
        datetime.fromisoformat("2026-08-22T09:52:00+02:00")
    )

    assert snapshot.source_available is True
    assert snapshot.tariff == "NT"
    assert snapshot.low_tariff_active is True
    assert snapshot.current_price == 4.07394
    assert snapshot.data_valid is True
    assert snapshot.next_switch is None
    assert snapshot.today_schedule == ()


def test_discovery_excludes_frakon_mirrored_schedule():
    assert _is_frakon_generated_schedule(
        "sensor.any_name", SimpleNamespace(platform="frakon_energy")
    ) is True
    assert _is_frakon_generated_schedule(
        "sensor.frakon_energy_hdo_dnesni_rozvrh", None
    ) is True
    assert _is_frakon_generated_schedule(
        "sensor.cez_hdo_schedule_home", SimpleNamespace(platform="cez_hdo")
    ) is False


def test_tariff_change_event_skips_first_refresh_and_fires_once():
    coordinator = object.__new__(CezHdoCoordinator)
    coordinator.source = _source()
    coordinator._last_tariff = None
    coordinator.hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()))

    nt = CezHdoSnapshot(
        low_tariff_active=True,
        tariff="NT",
        interval_start=None,
        interval_end=None,
        next_switch=datetime.fromisoformat("2026-08-04T23:50:00+02:00"),
        countdown_seconds=120,
        source_available=True,
        data_valid=True,
        current_price=4.67,
        today_schedule=(),
    )
    vt = CezHdoSnapshot(
        low_tariff_active=False,
        tariff="VT",
        interval_start=None,
        interval_end=None,
        next_switch=datetime.fromisoformat("2026-08-05T02:00:00+02:00"),
        countdown_seconds=7800,
        source_available=True,
        data_valid=True,
        current_price=7.51,
        today_schedule=(),
    )

    coordinator._emit_tariff_changed_event(nt)
    coordinator.hass.bus.async_fire.assert_not_called()

    coordinator._emit_tariff_changed_event(vt)
    coordinator.hass.bus.async_fire.assert_called_once()
    event_name, payload = coordinator.hass.bus.async_fire.call_args.args
    assert event_name == "frakon_energy_tariff_changed"
    assert payload["previous_tariff"] == "NT"
    assert payload["new_tariff"] == "VT"
    assert payload["low_tariff_active"] is False

    coordinator._emit_tariff_changed_event(vt)
    coordinator.hass.bus.async_fire.assert_called_once()


def test_unknown_tariff_does_not_reset_transition_baseline():
    coordinator = object.__new__(CezHdoCoordinator)
    coordinator.source = _source()
    coordinator._last_tariff = "NT"
    coordinator.hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()))

    unknown = CezHdoSnapshot(
        low_tariff_active=None,
        tariff="?",
        interval_start=None,
        interval_end=None,
        next_switch=None,
        countdown_seconds=None,
        source_available=False,
        data_valid=None,
        current_price=None,
        today_schedule=(),
    )

    coordinator._emit_tariff_changed_event(unknown)

    assert coordinator._last_tariff == "NT"
    coordinator.hass.bus.async_fire.assert_not_called()