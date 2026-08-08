from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from custom_components.frakon_energy.load_execution_final_phase_recheck import _settlement_baseline


class _States:
    def __init__(self, mapping):
        self.mapping = mapping

    def get(self, entity_id: str):
        return self.mapping.get(entity_id)


class _Hass:
    def __init__(self, mapping):
        self.states = _States(mapping)


def test_settlement_baseline_captures_exact_prestart_currents_and_timestamps() -> None:
    observed = datetime(2026, 8, 8, 20, 0, tzinfo=timezone.utc)
    hass = _Hass({
        "sensor.l1": SimpleNamespace(last_updated=observed),
        "sensor.l2": SimpleNamespace(last_updated=observed),
        "sensor.l3": SimpleNamespace(last_updated=observed),
    })
    lifecycle = SimpleNamespace(lifecycle_id="life-1", attempt_id="attempt-1")
    capacity = SimpleNamespace(phases={
        "L1": SimpleNamespace(current_a=10.0, source_entity_id="sensor.l1"),
        "L2": SimpleNamespace(current_a=11.0, source_entity_id="sensor.l2"),
        "L3": SimpleNamespace(current_a=12.0, source_entity_id="sensor.l3"),
    })

    result = _settlement_baseline(
        hass,  # type: ignore[arg-type]
        lifecycle=lifecycle,  # type: ignore[arg-type]
        capacity=capacity,  # type: ignore[arg-type]
        created_at=100,
    )

    assert result is not None
    assert result.baselines() == {"L1": 10.0, "L2": 11.0, "L3": 12.0}
    assert result.entities() == {"L1": "sensor.l1", "L2": "sensor.l2", "L3": "sensor.l3"}
    assert result.observed_at() == {
        "L1": observed.timestamp(),
        "L2": observed.timestamp(),
        "L3": observed.timestamp(),
    }
