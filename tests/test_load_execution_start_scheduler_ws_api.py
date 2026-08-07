from types import SimpleNamespace

import pytest

from custom_components.frakon_energy import load_execution_start_scheduler_ws_api as ws_api


class _Status:
    def as_dict(self):
        return {
            "attempt_id": "attempt-1",
            "status": "started_verified",
            "can_redispatch": False,
        }


@pytest.mark.asyncio
async def test_start_scheduler_status_is_read_only_and_reports_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = SimpleNamespace(
        started=True,
        healthy=True,
        last_error=None,
        statuses=lambda: (_Status(),),
    )
    monkeypatch.setattr(
        ws_api,
        "start_scheduler",
        lambda hass, entry_id: scheduler,
    )

    result = await ws_api.async_start_scheduler_status(
        object(),  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert result["started"] is True
    assert result["healthy"] is True
    assert result["autonomous_start_enabled"] is True
    assert result["creates_approval"] is False
    assert result["creates_attempt"] is False
    assert result["creates_lifecycle"] is False
    assert result["creates_stop_lease"] is False
    assert result["can_redispatch_unknown"] is False
    assert result["read_only"] is True
    assert result["statuses"][0]["can_redispatch"] is False


@pytest.mark.asyncio
async def test_start_scheduler_status_requires_entry_id() -> None:
    with pytest.raises(ValueError, match="entry_id is required"):
        await ws_api.async_start_scheduler_status(
            object(),  # type: ignore[arg-type]
            entry_id="",
        )
