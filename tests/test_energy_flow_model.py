from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.frakon_energy.energy_flow_model import (
    QUALITY_COMPLETE,
    QUALITY_NEEDS_SETUP,
    QUALITY_PARTIAL,
    build_energy_flow_snapshot,
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


class _Hass:
    def __init__(self, values: dict[str, tuple[str, str]]) -> None:
        self.states = _States(values)


def _options(
    *,
    battery: bool = False,
    ev: bool = False,
    wallbox: bool = False,
    battery_sign: str = "unknown",
    grid_scope: str = "whole_house",
    pv_scope: str = "gross_generation",
    ev_wallbox_relation: str = "unknown",
    confirm_pv: bool = True,
) -> dict:
    technologies = [
        {"id": "photovoltaics", "enabled": True, "entity_ids": []},
        {"id": "smart_meter", "enabled": True, "entity_ids": []},
    ]
    assignments = [
        {
            "technology": "photovoltaics",
            "role": "pv_power",
            "entity_id": "sensor.pv_power",
            "confirmed": confirm_pv,
        },
        {
            "technology": "smart_meter",
            "role": "grid_import",
            "entity_id": "sensor.grid_import",
            "confirmed": True,
        },
        {
            "technology": "smart_meter",
            "role": "grid_export",
            "entity_id": "sensor.grid_export",
            "confirmed": True,
        },
    ]
    if battery:
        technologies.append({"id": "home_battery", "enabled": True, "entity_ids": []})
        assignments.append(
            {
                "technology": "home_battery",
                "role": "power",
                "entity_id": "sensor.battery_power",
                "confirmed": True,
            }
        )
    if ev:
        technologies.append({"id": "electric_vehicle", "enabled": True, "entity_ids": []})
        assignments.append(
            {
                "technology": "electric_vehicle",
                "role": "power",
                "entity_id": "sensor.ev_power",
                "confirmed": True,
            }
        )
    if wallbox:
        technologies.append({"id": "wallbox", "enabled": True, "entity_ids": []})
        assignments.append(
            {
                "technology": "wallbox",
                "role": "power",
                "entity_id": "sensor.wallbox_power",
                "confirmed": True,
            }
        )
    return {
        "technologies": technologies,
        "entity_assignments": {"version": 1, "items": assignments},
        "energy_flow": {
            "battery_power_sign": battery_sign,
            "grid_meter_scope": grid_scope,
            "pv_power_scope": pv_scope,
            "ev_wallbox_relation": ev_wallbox_relation,
        },
    }


def _base_states() -> dict[str, tuple[str, str]]:
    return {
        "sensor.pv_power": ("3", "kW"),
        "sensor.grid_import": ("1000", "W"),
        "sensor.grid_export": ("0.5", "kW"),
    }


def test_complete_whole_house_balance_without_battery() -> None:
    result = build_energy_flow_snapshot(
        _Hass(_base_states()),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(),
    )

    assert result.quality == QUALITY_COMPLETE
    assert result.house_load_kw == pytest.approx(3.5)
    assert result.pv_generation_kw == pytest.approx(3.0)
    assert result.grid_import_kw == pytest.approx(1.0)
    assert result.grid_export_kw == pytest.approx(0.5)
    assert result.battery_charge_kw is None
    assert result.battery_discharge_kw is None
    assert result.service_call_performed is False
    assert result.execution_performed is False


@pytest.mark.parametrize(
    ("sign", "battery_state", "expected_charge", "expected_discharge", "expected_house"),
    [
        ("positive_is_charge", "1", 1.0, 0.0, 2.5),
        ("positive_is_discharge", "1", 0.0, 1.0, 4.5),
        ("positive_is_charge", "-1", 0.0, 1.0, 4.5),
        ("positive_is_discharge", "-1", 1.0, 0.0, 2.5),
    ],
)
def test_battery_sign_semantics_are_explicit_and_deterministic(
    sign: str,
    battery_state: str,
    expected_charge: float,
    expected_discharge: float,
    expected_house: float,
) -> None:
    states = _base_states()
    states["sensor.battery_power"] = (battery_state, "kW")
    result = build_energy_flow_snapshot(
        _Hass(states),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(battery=True, battery_sign=sign),
    )

    assert result.quality == QUALITY_COMPLETE
    assert result.battery_charge_kw == pytest.approx(expected_charge)
    assert result.battery_discharge_kw == pytest.approx(expected_discharge)
    assert result.house_load_kw == pytest.approx(expected_house)


def test_enabled_battery_without_sign_fails_closed() -> None:
    states = _base_states()
    states["sensor.battery_power"] = ("1", "kW")
    result = build_energy_flow_snapshot(
        _Hass(states),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(battery=True, battery_sign="unknown"),
    )

    assert result.quality == QUALITY_NEEDS_SETUP
    assert result.house_load_kw is None
    assert any("znaménka" in reason for reason in result.reasons)


def test_unconfirmed_required_assignment_is_not_trusted() -> None:
    result = build_energy_flow_snapshot(
        _Hass(_base_states()),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(confirm_pv=False),
    )

    assert result.quality == QUALITY_NEEDS_SETUP
    assert result.house_load_kw is None
    assert result.entities["pv"].entity_id is None


def test_confirmed_topology_with_temporarily_bad_live_unit_is_partial_not_setup() -> None:
    states = _base_states()
    states["sensor.pv_power"] = ("3000", "Wh")
    result = build_energy_flow_snapshot(
        _Hass(states),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(),
    )

    assert result.quality == QUALITY_PARTIAL
    assert result.house_load_kw is None
    assert result.entities["pv"].reason == "unsupported_power_unit"


def test_unconfirmed_topology_never_derives_house_load() -> None:
    result = build_energy_flow_snapshot(
        _Hass(_base_states()),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(grid_scope="unknown", pv_scope="unknown"),
    )

    assert result.quality == QUALITY_NEEDS_SETUP
    assert result.house_load_kw is None
    assert len(result.reasons) >= 2


def test_ev_wallbox_same_flow_is_not_double_counted() -> None:
    states = _base_states()
    states.update(
        {
            "sensor.ev_power": ("7.4", "kW"),
            "sensor.wallbox_power": ("7.4", "kW"),
        }
    )
    result = build_energy_flow_snapshot(
        _Hass(states),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(ev=True, wallbox=True, ev_wallbox_relation="same_flow"),
    )

    assert result.quality == QUALITY_COMPLETE
    assert result.known_load_kw == pytest.approx(7.4)
    assert result.known_load_quality == QUALITY_COMPLETE


def test_ev_wallbox_separate_flows_are_summed() -> None:
    states = _base_states()
    states.update(
        {
            "sensor.ev_power": ("7.4", "kW"),
            "sensor.wallbox_power": ("7.4", "kW"),
        }
    )
    result = build_energy_flow_snapshot(
        _Hass(states),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(ev=True, wallbox=True, ev_wallbox_relation="separate"),
    )

    assert result.quality == QUALITY_COMPLETE
    assert result.known_load_kw == pytest.approx(14.8)


def test_unknown_ev_wallbox_relation_preserves_house_balance_but_marks_breakdown_partial() -> None:
    states = _base_states()
    states.update(
        {
            "sensor.ev_power": ("7.4", "kW"),
            "sensor.wallbox_power": ("7.4", "kW"),
        }
    )
    result = build_energy_flow_snapshot(
        _Hass(states),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(ev=True, wallbox=True, ev_wallbox_relation="unknown"),
    )

    assert result.quality == QUALITY_PARTIAL
    assert result.house_load_kw == pytest.approx(3.5)
    assert result.known_load_kw == pytest.approx(14.8)
    assert result.known_load_quality == QUALITY_PARTIAL
