from __future__ import annotations

import pytest
from homeassistant.components import panel_custom

from custom_components import frakon_energy
from custom_components.frakon_energy import panel


class _Http:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    async def async_register_static_paths(self, configs) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("static registration failed")


class _Hass:
    def __init__(self) -> None:
        self.data: dict = {}
        self.http = _Http()


@pytest.mark.asyncio
async def test_global_setup_registers_sidebar_without_config_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    panel_calls = 0

    async def register_panel(*args, **kwargs) -> None:
        nonlocal panel_calls
        panel_calls += 1

    monkeypatch.setattr(panel_custom, "async_register_panel", register_panel)

    assert await frakon_energy.async_setup(hass, {}) is True  # type: ignore[arg-type]
    assert hass.http.calls == 1
    assert panel_calls == 1
    assert hass.data[panel._STATIC_PATHS_REGISTERED_KEY] is True
    assert hass.data[panel._PANEL_REGISTERED_KEY] is True


@pytest.mark.asyncio
async def test_retry_after_panel_failure_does_not_reregister_static_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    panel_calls = 0
    fail_panel = True

    async def register_panel(*args, **kwargs) -> None:
        nonlocal panel_calls, fail_panel
        panel_calls += 1
        if fail_panel:
            raise RuntimeError("panel registration failed")

    monkeypatch.setattr(panel_custom, "async_register_panel", register_panel)

    with pytest.raises(RuntimeError, match="panel registration failed"):
        await panel.async_register_panel(hass)  # type: ignore[arg-type]

    assert hass.http.calls == 1
    assert hass.data[panel._STATIC_PATHS_REGISTERED_KEY] is True
    assert panel._PANEL_REGISTERED_KEY not in hass.data

    fail_panel = False
    await panel.async_register_panel(hass)  # type: ignore[arg-type]

    assert hass.http.calls == 1
    assert panel_calls == 2
    assert hass.data[panel._PANEL_REGISTERED_KEY] is True

    await panel.async_register_panel(hass)  # type: ignore[arg-type]
    assert hass.http.calls == 1
    assert panel_calls == 2


@pytest.mark.asyncio
async def test_failed_static_path_registration_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    hass.http.fail = True
    panel_calls = 0

    async def register_panel(*args, **kwargs) -> None:
        nonlocal panel_calls
        panel_calls += 1

    monkeypatch.setattr(panel_custom, "async_register_panel", register_panel)

    with pytest.raises(RuntimeError, match="static registration failed"):
        await panel.async_register_panel(hass)  # type: ignore[arg-type]

    assert hass.http.calls == 1
    assert panel._STATIC_PATHS_REGISTERED_KEY not in hass.data
    assert panel_calls == 0

    hass.http.fail = False
    await panel.async_register_panel(hass)  # type: ignore[arg-type]

    assert hass.http.calls == 2
    assert panel_calls == 1
    assert hass.data[panel._STATIC_PATHS_REGISTERED_KEY] is True
    assert hass.data[panel._PANEL_REGISTERED_KEY] is True
