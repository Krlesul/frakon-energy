from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.frakon_energy import load_execution_runtime_lifecycle as lifecycle
from custom_components.frakon_energy.const import DOMAIN


@pytest.mark.asyncio
async def test_shutdown_purges_entry_caches_even_when_a_stopper_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "load_execution_lifecycle_repositories_by_entry": {
                    "entry-1": object(),
                    "entry-2": object(),
                },
                "load_execution_phase_settlement_confirmation_repositories_by_entry": {
                    "entry-1": object(),
                },
            }
        }
    )

    async def fail_settlement(hass_arg, entry_id: str) -> None:
        calls.append("settlement")
        raise RuntimeError("settlement stop failed")

    async def ok_pending(hass_arg, entry_id: str) -> None:
        calls.append("pending")

    async def ok_start(hass_arg, entry_id: str) -> None:
        calls.append("start")

    async def ok_stop(hass_arg, entry_id: str) -> None:
        calls.append("stop")

    monkeypatch.setattr(lifecycle, "async_stop_phase_settlement_runtime", fail_settlement)
    monkeypatch.setattr(lifecycle, "async_stop_pending_run_scheduler", ok_pending)
    monkeypatch.setattr(lifecycle, "async_stop_start_scheduler", ok_start)
    monkeypatch.setattr(lifecycle, "async_stop_stop_scheduler", ok_stop)

    with pytest.raises(RuntimeError, match="settlement stop failed"):
        await lifecycle.async_stop_execution_runtimes(hass, "entry-1")  # type: ignore[arg-type]

    assert calls == ["settlement", "pending", "start", "stop"]
    assert "entry-1" not in hass.data[DOMAIN]["load_execution_lifecycle_repositories_by_entry"]
    assert "entry-2" in hass.data[DOMAIN]["load_execution_lifecycle_repositories_by_entry"]
    assert "entry-1" not in hass.data[DOMAIN]["load_execution_phase_settlement_confirmation_repositories_by_entry"]
