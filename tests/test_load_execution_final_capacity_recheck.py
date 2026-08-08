from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_final_capacity_recheck as final_recheck
from custom_components.frakon_energy.load_execution_capacity_reservation import (
    CapacityReservationRepository,
)
from custom_components.frakon_energy.load_execution_final_capacity_recheck import (
    FINAL_RECHECK_BLOCKED,
    FINAL_RECHECK_BYPASSED,
    FINAL_RECHECK_READY,
    async_final_capacity_recheck,
)
from custom_components.frakon_energy.load_execution_site_capacity_gate import (
    REASON_INSUFFICIENT_HEADROOM,
)


class _States:
    def __init__(self, values: dict[str, tuple[str, str]]) -> None:
        self.values = values

    def get(self, entity_id: str):
        value = self.values.get(entity_id)
        if value is None:
            return None
        state, unit = value
        return SimpleNamespace(state=state, attributes={"unit_of_measurement": unit})


class _ConfigEntries:
    def __init__(self, entry: Any) -> None:
        self.entry = entry

    def async_get_entry(self, entry_id: str):
        return self.entry if entry_id == self.entry.entry_id else None


class _Hass:
    def __init__(self, entry: Any, grid_import_kw: float) -> None:
        self.data: dict[str, Any] = {}
        self.states = _States(
            {
                "sensor.pv": ("3", "kW"),
                "sensor.grid_in": (str(grid_import_kw), "kW"),
                "sensor.grid_out": ("0", "kW"),
            }
        )
        self.config_entries = _ConfigEntries(entry)


class _Store:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = data


def _options(limit: float | None) -> dict[str, Any]:
    options: dict[str, Any] = {
        "technologies": [
            {"id": "photovoltaics", "enabled": True, "entity_ids": []},
            {"id": "smart_meter", "enabled": True, "entity_ids": []},
        ],
        "entity_assignments": {
            "version": 1,
            "items": [
                {
                    "technology": "photovoltaics",
                    "role": "pv_power",
                    "entity_id": "sensor.pv",
                    "confirmed": True,
                },
                {
                    "technology": "smart_meter",
                    "role": "grid_import",
                    "entity_id": "sensor.grid_in",
                    "confirmed": True,
                },
                {
                    "technology": "smart_meter",
                    "role": "grid_export",
                    "entity_id": "sensor.grid_out",
                    "confirmed": True,
                },
            ],
        },
        "energy_flow": {
            "battery_power_sign": "unknown",
            "grid_meter_scope": "whole_house",
            "pv_power_scope": "gross_generation",
            "ev_wallbox_relation": "unknown",
        },
    }
    if limit is not None:
        options["site_capacity"] = {"max_grid_import_kw": limit}
    return options


def _entry(limit: float | None):
    return SimpleNamespace(
        entry_id="entry-1",
        domain="frakon_energy",
        options=_options(limit),
    )


def _dispatching(
    power_kw: float = 11.0,
    *,
    lifecycle_id: str = "lifecycle-1",
    attempt_id: str = "attempt-1",
):
    return SimpleNamespace(
        state="dispatching",
        lifecycle_id=lifecycle_id,
        attempt_id=attempt_id,
        plan=SimpleNamespace(power_kw=power_kw),
        validated=lambda: None,
    )


def _wire_reservations(monkeypatch: pytest.MonkeyPatch) -> CapacityReservationRepository:
    repository = CapacityReservationRepository(_Store())
    monkeypatch.setattr(
        final_recheck,
        "capacity_reservation_repository",
        lambda hass, entry_id: repository,
    )
    monkeypatch.setattr(final_recheck.time, "time", lambda: 1_800_000_000)
    return repository


@pytest.mark.asyncio
async def test_live_grid_rise_between_gate_and_boundary_turns_ready_into_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry(15.0)
    hass = _Hass(entry, grid_import_kw=2.0)
    _wire_reservations(monkeypatch)

    async def dispatching_records(hass_obj: Any, entry_id: str):
        return [_dispatching(11.0)]

    monkeypatch.setattr(final_recheck, "_dispatching_records", dispatching_records)

    first = await async_final_capacity_recheck(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
    )
    assert first.status == FINAL_RECHECK_READY
    assert first.can_start is True
    assert first.reservation is not None
    assert first.state_transition_performed is True
    assert first.capacity_gate is not None
    assert first.capacity_gate["current_grid_import_kw"] == pytest.approx(2.0)
    assert first.capacity_gate["projected_grid_import_kw"] == pytest.approx(13.0)

    hass.states.values["sensor.grid_in"] = ("8", "kW")

    second = await async_final_capacity_recheck(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
    )
    assert second.status == FINAL_RECHECK_BLOCKED
    assert second.can_start is False
    assert second.reason == REASON_INSUFFICIENT_HEADROOM
    assert second.reserved_other_power_kw == pytest.approx(0.0)
    assert second.capacity_gate is not None
    assert second.capacity_gate["current_grid_import_kw"] == pytest.approx(8.0)
    assert second.capacity_gate["grid_headroom_kw"] == pytest.approx(7.0)
    assert second.capacity_gate["projected_grid_import_kw"] == pytest.approx(19.0)
    assert second.capacity_gate["projected_over_limit_kw"] == pytest.approx(4.0)
    assert second.service_call_performed is False
    assert second.execution_performed is False


@pytest.mark.asyncio
async def test_second_start_cannot_reuse_capacity_reserved_by_first_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry(19.0)
    hass = _Hass(entry, grid_import_kw=2.0)
    _wire_reservations(monkeypatch)
    current = [_dispatching(11.0, lifecycle_id="life-a", attempt_id="attempt-a")]

    async def dispatching_records(hass_obj: Any, entry_id: str):
        return list(current)

    monkeypatch.setattr(final_recheck, "_dispatching_records", dispatching_records)

    first = await async_final_capacity_recheck(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
    )
    assert first.status == FINAL_RECHECK_READY
    assert first.capacity_gate is not None
    assert first.capacity_gate["projected_grid_import_kw"] == pytest.approx(13.0)

    # Meter is deliberately still stale at 2 kW. A second 7 kW load would look
    # safe from meter data alone (2 + 7 = 9), but the first 11 kW is reserved.
    current[:] = [_dispatching(7.0, lifecycle_id="life-b", attempt_id="attempt-b")]
    second = await async_final_capacity_recheck(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert second.status == FINAL_RECHECK_BLOCKED
    assert second.reason == REASON_INSUFFICIENT_HEADROOM
    assert second.can_start is False
    assert second.reserved_other_power_kw == pytest.approx(11.0)
    assert second.effective_planned_power_kw == pytest.approx(18.0)
    assert second.capacity_gate is not None
    assert second.capacity_gate["projected_grid_import_kw"] == pytest.approx(20.0)
    assert second.capacity_gate["projected_over_limit_kw"] == pytest.approx(1.0)
    assert second.reservation is None


@pytest.mark.asyncio
async def test_unconfigured_capacity_bypasses_without_reservation_or_dispatching_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry(None)
    hass = _Hass(entry, grid_import_kw=99.0)
    looked_up = False
    reservation_lookup = False

    async def dispatching_records(hass_obj: Any, entry_id: str):
        nonlocal looked_up
        looked_up = True
        return []

    def reservation_repository(hass_obj: Any, entry_id: str):
        nonlocal reservation_lookup
        reservation_lookup = True
        raise AssertionError("reservation repository must not be used")

    monkeypatch.setattr(final_recheck, "_dispatching_records", dispatching_records)
    monkeypatch.setattr(final_recheck, "capacity_reservation_repository", reservation_repository)

    result = await async_final_capacity_recheck(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert result.status == FINAL_RECHECK_BYPASSED
    assert result.can_start is True
    assert result.guard_active is False
    assert looked_up is False
    assert reservation_lookup is False
    assert result.reservation is None
    assert result.state_transition_performed is False
    assert result.service_call_performed is False
    assert result.execution_performed is False
