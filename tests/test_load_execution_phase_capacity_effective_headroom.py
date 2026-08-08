from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.frakon_energy import (
    load_execution_phase_capacity_reservation_status as status,
)


class _ConfigEntries:
    def async_get_entry(self, entry_id: str):
        return SimpleNamespace(entry_id=entry_id, domain="frakon_energy", options={})


class _Hass:
    def __init__(self) -> None:
        self.config_entries = _ConfigEntries()


def _capacity():
    return SimpleNamespace(
        configured=True,
        source_ready=True,
        phases={
            "L1": SimpleNamespace(current_a=10.0, max_current_a=25.0),
            "L2": SimpleNamespace(current_a=20.0, max_current_a=25.0),
            "L3": SimpleNamespace(current_a=24.0, max_current_a=25.0),
        },
    )


def test_effective_headroom_subtracts_active_reservations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(status, "build_site_phase_capacity_status", lambda *args, **kwargs: _capacity())

    healthy, error, current, headroom, over = status._effective_capacity(
        _Hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        reserved_current_a={"L1": 6.0, "L2": 2.0, "L3": 3.0},
    )

    assert healthy is True
    assert error is None
    assert current == {"L1": 16.0, "L2": 22.0, "L3": 27.0}
    assert headroom == {"L1": 9.0, "L2": 3.0, "L3": 0.0}
    assert over == {"L1": 0.0, "L2": 0.0, "L3": 2.0}


def test_effective_headroom_is_unknown_when_phase_capacity_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capacity = _capacity()
    capacity.source_ready = False
    monkeypatch.setattr(status, "build_site_phase_capacity_status", lambda *args, **kwargs: capacity)

    healthy, error, current, headroom, over = status._effective_capacity(
        _Hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        reserved_current_a={"L1": 1.0, "L2": 1.0, "L3": 1.0},
    )

    assert healthy is True
    assert error is None
    assert current == {"L1": None, "L2": None, "L3": None}
    assert headroom == {"L1": None, "L2": None, "L3": None}
    assert over == {"L1": None, "L2": None, "L3": None}
