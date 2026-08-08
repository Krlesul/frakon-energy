from pathlib import Path


def test_safety_status_websocket_exposes_phase_reservations() -> None:
    source = Path(
        "custom_components/frakon_energy/load_execution_safety_status_ws_api.py"
    ).read_text()

    assert "async_phase_capacity_reservation_status" in source
    assert 'result["site_phase_capacity_reservations"]' in source
    assert "entry_id=msg[\"entry_id\"]" in source
