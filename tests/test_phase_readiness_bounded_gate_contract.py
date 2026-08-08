from pathlib import Path


def test_bounded_gate_wires_phase_readiness_fail_closed_when_configured():
    source = Path(
        "custom_components/frakon_energy/load_execution_bounded_dispatch_gate_ws_api.py"
    ).read_text()
    assert "build_load_phase_readiness" in source
    assert "phase_guard_active = phase_capacity.configured" in source
    assert "phase_guard_active and not phase_readiness.can_start_phase" in source
    assert '"phase_readiness": phase_readiness.as_dict()' in source
    assert '"phase_guard_active": phase_guard_active' in source
