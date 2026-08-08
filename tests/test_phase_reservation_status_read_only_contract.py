from pathlib import Path


def test_phase_reservation_status_is_read_only() -> None:
    source = Path(
        "custom_components/frakon_energy/load_execution_phase_capacity_reservation_status.py"
    ).read_text()

    assert ".async_snapshot(" in source
    assert ".async_active(" not in source
    assert ".async_reserve(" not in source
