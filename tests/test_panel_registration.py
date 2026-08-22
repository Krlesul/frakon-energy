from __future__ import annotations

import pytest
from homeassistant.components import frontend
from homeassistant.components.http.server import HomeAssistantHTTP
from homeassistant.core import CoreState
from homeassistant.helpers.http import KEY_ALLOW_CONFIGURED_CORS

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


class _Bus:
    def __init__(self) -> None:
        self.fired: list[str] = []
        self.once: dict[str, object] = {}

    def async_fire(self, event_type: str, event_data=None) -> None:
        self.fired.append(event_type)

    def async_listen_once(self, event_type: str, listener):
        self.once[event_type] = listener
        return lambda: self.once.pop(event_type, None)


class _Hass:
    def __init__(self) -> None:
        self.data: dict = {}
        self.http = _Http()
        self.bus = _Bus()
        self.state = CoreState.starting


class _ExecutorHass(_Hass):
    async def async_add_executor_job(self, target, *args):
        return target(*args)


@pytest.mark.asyncio
async def test_global_setup_registers_real_sidebar_panel_without_config_entry() -> None:
    hass = _Hass()

    assert await frakon_energy.async_setup(hass, {}) is True  # type: ignore[arg-type]
    assert hass.http.calls == 1
    assert panel.panel_is_registered(hass) is True

    registered = hass.data[frontend.DATA_PANELS][panel.PANEL_URL_PATH]
    response = registered.to_response()
    assert response["title"] == "FRAKON Energy"
    assert response["icon"] == "mdi:lightning-bolt-circle"
    assert response["url_path"] == "frakon-energy"
    assert response["show_in_sidebar"] is True
    assert response["default_visible"] is True
    assert response["config"]["_panel_custom"]["module_url"] == panel.PANEL_MODULE_URL


@pytest.mark.asyncio
async def test_registration_is_idempotent_against_actual_frontend_registry() -> None:
    hass = _Hass()

    await panel.async_register_panel(hass)  # type: ignore[arg-type]
    first = hass.data[frontend.DATA_PANELS][panel.PANEL_URL_PATH]
    await panel.async_register_panel(hass)  # type: ignore[arg-type]

    assert hass.http.calls == 1
    assert hass.data[frontend.DATA_PANELS][panel.PANEL_URL_PATH] is first


@pytest.mark.asyncio
async def test_post_start_reconcile_repairs_panel_removed_during_bootstrap() -> None:
    hass = _Hass()

    await panel.async_register_panel(hass)  # type: ignore[arg-type]
    assert panel.panel_is_registered(hass) is True
    assert "homeassistant_started" in hass.bus.once

    del hass.data[frontend.DATA_PANELS][panel.PANEL_URL_PATH]
    assert panel.panel_is_registered(hass) is False

    hass.state = CoreState.running
    listener = hass.bus.once["homeassistant_started"]
    await listener(None)  # type: ignore[operator]

    assert panel.panel_is_registered(hass) is True
    assert hass.http.calls == 1
    assert panel._PANEL_STARTUP_RECONCILE_KEY not in hass.data


@pytest.mark.asyncio
async def test_missing_panel_is_self_healed_on_later_registration_call() -> None:
    hass = _Hass()
    hass.state = CoreState.running

    await panel.async_register_panel(hass)  # type: ignore[arg-type]
    del hass.data[frontend.DATA_PANELS][panel.PANEL_URL_PATH]

    await panel.async_register_panel(hass)  # type: ignore[arg-type]

    assert panel.panel_is_registered(hass) is True
    assert hass.http.calls == 1


@pytest.mark.asyncio
async def test_static_path_failure_cannot_remove_sidebar_route_and_is_retried() -> None:
    hass = _Hass()
    hass.http.fail = True

    # The actual HA panel route is registered before optional asset serving.
    await panel.async_register_panel(hass)  # type: ignore[arg-type]
    assert hass.http.calls == 1
    assert panel._STATIC_PATHS_REGISTERED_KEY not in hass.data
    assert panel.panel_is_registered(hass) is True

    hass.http.fail = False
    await panel.async_register_panel(hass)  # type: ignore[arg-type]

    assert hass.http.calls == 2
    assert hass.data[panel._STATIC_PATHS_REGISTERED_KEY] is True
    assert panel.panel_is_registered(hass) is True


@pytest.mark.asyncio
async def test_real_home_assistant_http_router_accepts_both_static_roots() -> None:
    """Exercise the real HA aiohttp static-route implementation, not a mock."""
    hass = _ExecutorHass()
    http = HomeAssistantHTTP(
        hass,  # type: ignore[arg-type]
        ssl_certificate=None,
        ssl_peer_certificate=None,
        ssl_key=None,
        server_host=["127.0.0.1"],
        server_port=8123,
        trusted_proxies=[],
        ssl_profile="modern",
    )
    http.app[KEY_ALLOW_CONFIGURED_CORS] = lambda _resource: None
    hass.http = http  # type: ignore[assignment]

    await panel.async_register_panel(hass)  # type: ignore[arg-type]

    assert panel.panel_is_registered(hass) is True
    assert hass.data[panel._STATIC_PATHS_REGISTERED_KEY] is True
    assert not panel.PANEL_APP_STATIC_URL.startswith(
        f"{panel.PANEL_MODULE_STATIC_URL}/"
    )
    assert not panel.PANEL_MODULE_STATIC_URL.startswith(
        f"{panel.PANEL_APP_STATIC_URL}/"
    )

    canonicals = {resource.canonical for resource in http.app.router.resources()}
    assert panel.PANEL_MODULE_STATIC_URL in canonicals
    assert panel.PANEL_APP_STATIC_URL in canonicals
