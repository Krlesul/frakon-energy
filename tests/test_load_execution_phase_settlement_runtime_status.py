from __future__ import annotations

from custom_components.frakon_energy.const import DOMAIN
from custom_components.frakon_energy.load_execution_phase_settlement_runtime import (
    PhaseSettlementRuntime,
    _RUNTIME_KEY,
)
from custom_components.frakon_energy.load_execution_phase_settlement_runtime_status import (
    phase_settlement_runtime_status,
)


class _Hass:
    def __init__(self) -> None:
        self.data = {}


def test_missing_runtime_status_is_read_only_and_does_not_create_domain_state() -> None:
    hass = _Hass()

    result = phase_settlement_runtime_status(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert result["started"] is False
    assert result["healthy"] is False
    assert result["statuses"] == []
    assert result["read_only"] is True
    assert result["service_call_performed"] is False
    assert result["execution_performed"] is False
    assert hass.data == {}


def test_existing_runtime_status_is_exposed_without_starting_it() -> None:
    hass = _Hass()
    runtime = PhaseSettlementRuntime(hass, "entry-1")  # type: ignore[arg-type]
    hass.data[DOMAIN] = {_RUNTIME_KEY: {"entry-1": runtime}}

    result = phase_settlement_runtime_status(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert result["started"] is False
    assert result["healthy"] is True
    assert result["last_error"] is None
    assert result["statuses"] == []
    assert result["poll_seconds"] == 5
