from __future__ import annotations

from types import SimpleNamespace

import pytest

import custom_components.frakon_energy as integration


@pytest.mark.asyncio
async def test_sidebar_panel_is_registered_before_provider_refresh_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def register_panel(_hass) -> None:
        calls.append("panel")

    class FailingCoordinator:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def async_initialize_history(self) -> None:
            calls.append("history")

        async def async_config_entry_first_refresh(self) -> None:
            calls.append("refresh")
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(integration, "async_register_panel", register_panel)
    monkeypatch.setattr(integration, "FrakonEnergyCoordinator", FailingCoordinator)
    monkeypatch.setattr(integration, "VisionQApiClient", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: object())

    entry = SimpleNamespace(
        data={"username": "test", "password": "test"},
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await integration.async_setup_entry(object(), entry)  # type: ignore[arg-type]

    assert calls == ["panel", "history", "refresh"]
