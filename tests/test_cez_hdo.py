from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

from homeassistant.util import dt as dt_util

from custom_components.frakon_energy.hdo_coordinator import CezHdoCoordinator
from custom_components.frakon_energy.providers.cez_hdo import CezHdoAdapter, CezHdoSnapshot
from custom_components.frakon_energy.providers.cez_hdo_discovery import CezHdoSource


class FakeStates:
    def __init__(self, states):
        self._states = states

    def get(self, entity_id):
        return self._states.get(entity_id)


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
