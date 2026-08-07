from types import SimpleNamespace

import pytest

from custom_components.frakon_energy import load_execution_stop_scheduler_ws_api as ws_api


class _FakeScheduler:
    started = True
    healthy = True
    last_error = None

    def statuses(self):
        return (
            SimpleNamespace(
                start_lifecycle_id="start-1",
                dispatch_required=True,
                as_dict=lambda: {
                    "start_lifecycle_id": "start-1",
                    "status": "ready_to_stop",
                    "dispatch_required": True,
                    "service_call_performed": False,
                    "execution_performed": False,
                    "executor_available": False,
                },
            ),
            SimpleNamespace(
                start_lifecycle_id="start-2",
                dispatch_required=False,
                as_dict=lambda: {
                    "start_lifecycle_id": "start-2",
                    "status": "scheduled",
                    "dispatch_required": False,
                    "service_call_performed": False,
                    "execution_performed": False,
                    "executor_available": False,
                },
            ),
        )


@pytest.mark.asyncio
async def test_status_api_is_read_only_and_surfaces_only_dispatch_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ws_api, "stop_scheduler", lambda hass, entry_id: _FakeScheduler())

    result = await ws_api.async_stop_scheduler_status(object(), entry_id="entry-1")  # type: ignore[arg-type]

    assert result["started"] is True
    assert result["healthy"] is True
    assert result["ready_to_stop"] == ["start-1"]
    assert result["read_only"] is True
    assert result["state_transition_performed"] is False
    assert result["service_call_performed"] is False
    assert result["execution_performed"] is False
    assert result["executor_available"] is False
