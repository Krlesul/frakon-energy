from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, Event, HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "frakon-energy"
PANEL_STATIC_URL = "/frakon-energy-static"
PANEL_MODULE_URL = f"{PANEL_STATIC_URL}/panel.js"
PANEL_TITLE = "FRAKON Energy"
PANEL_ICON = "mdi:lightning-bolt-circle"

_STATIC_PATHS_REGISTERED_KEY = f"{DOMAIN}_panel_static_paths_registered"
_PANEL_LOCK_KEY = f"{DOMAIN}_panel_registration_lock"
_PANEL_STARTUP_RECONCILE_KEY = f"{DOMAIN}_panel_startup_reconcile_registered"


def panel_is_registered(hass: HomeAssistant) -> bool:
    """Return whether Home Assistant currently exposes the FRAKON panel."""
    panels = hass.data.get(frontend.DATA_PANELS)
    return isinstance(panels, dict) and PANEL_URL_PATH in panels


def _registration_lock(hass: HomeAssistant) -> asyncio.Lock:
    lock = hass.data.get(_PANEL_LOCK_KEY)
    if isinstance(lock, asyncio.Lock):
        return lock
    lock = asyncio.Lock()
    hass.data[_PANEL_LOCK_KEY] = lock
    return lock


async def async_register_panel(hass: HomeAssistant) -> None:
    """Ensure FRAKON Energy static assets and sidebar panel really exist.

    The source of truth is Home Assistant's actual frontend panel registry.  We do
    not cache a separate "registered" boolean: such a flag can become stale when
    Home Assistant rebuilds the frontend registry during startup or after a
    frontend reload.
    """
    async with _registration_lock(hass):
        frontend_dir = Path(__file__).parent / "frontend"
        app_dir = Path(__file__).parent / "frontend_app"

        if not hass.data.get(_STATIC_PATHS_REGISTERED_KEY):
            await hass.http.async_register_static_paths(
                [
                    StaticPathConfig(PANEL_STATIC_URL, str(frontend_dir), False),
                    StaticPathConfig(
                        f"{PANEL_STATIC_URL}/app",
                        str(app_dir),
                        False,
                    ),
                ]
            )
            # Static routes persist for the Home Assistant process lifetime.
            hass.data[_STATIC_PATHS_REGISTERED_KEY] = True

        if panel_is_registered(hass):
            return

        await panel_custom.async_register_panel(
            hass,
            webcomponent_name="frakon-energy-panel",
            frontend_url_path=PANEL_URL_PATH,
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            module_url=PANEL_MODULE_URL,
            embed_iframe=False,
            require_admin=False,
        )

        if not panel_is_registered(hass):
            raise RuntimeError(
                "FRAKON Energy panel registration returned without adding the "
                "panel to the Home Assistant frontend registry"
            )

        _LOGGER.info("FRAKON Energy sidebar panel registered")


async def _async_reconcile_panel_after_start(
    hass: HomeAssistant, _event: Event | None = None
) -> None:
    """Repair a panel lost while Home Assistant finishes frontend bootstrap."""
    hass.data.pop(_PANEL_STARTUP_RECONCILE_KEY, None)
    try:
        await async_register_panel(hass)
    except Exception:  # panel failure must never take energy sensors down
        _LOGGER.exception(
            "Unable to reconcile FRAKON Energy sidebar panel after Home Assistant startup"
        )


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register the panel now and reconcile once after HA has fully started.

    Config-entry integrations can be initialized while Home Assistant is still
    constructing the frontend.  Registering only at config-entry setup time can
    therefore be lost later in the same bootstrap.  A one-shot post-start check
    makes the registration deterministic and self-healing without polling.
    """
    try:
        await async_register_panel(hass)
    except Exception:
        # Keep VisionQ/HDO/sensors operational even if the optional UI shell has a
        # transient startup problem.  The post-start reconciliation retries it.
        _LOGGER.exception("Unable to register FRAKON Energy sidebar panel")

    if hass.state is CoreState.running:
        # On config-entry reload Home Assistant is already running, so the call
        # above is the final authoritative reconciliation.
        return

    if hass.data.get(_PANEL_STARTUP_RECONCILE_KEY):
        return

    hass.data[_PANEL_STARTUP_RECONCILE_KEY] = True

    async def _handle_started(event: Event) -> None:
        await _async_reconcile_panel_after_start(hass, event)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _handle_started)
