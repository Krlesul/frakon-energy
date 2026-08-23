from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import re
from urllib.parse import quote

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, Event, HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "frakon-energy"
PANEL_MODULE_STATIC_URL = "/frakon-energy-panel-static"
PANEL_APP_STATIC_URL = "/frakon-energy-app-static"
PANEL_TITLE = "FRAKON Energy"
PANEL_ICON = "mdi:lightning-bolt-circle"


def _load_panel_version() -> str:
    """Return the installed integration version used to identify frontend assets."""
    try:
        manifest_path = Path(__file__).parent / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        version = str(manifest.get("version", "")).strip()
    except (OSError, ValueError, TypeError):
        _LOGGER.exception("Unable to read FRAKON Energy version for panel assets")
        return "unversioned"

    if not version:
        _LOGGER.warning("FRAKON Energy manifest has no version; panel assets are unversioned")
        return "unversioned"
    return version


def _webcomponent_name_for_version(version: str) -> str:
    """Return a valid, deterministic custom-element name for one release."""
    token = re.sub(r"[^a-z0-9]+", "-", version.lower()).strip("-")
    return f"frakon-energy-panel-{token or 'unversioned'}"


PANEL_VERSION = _load_panel_version()
PANEL_ASSET_VERSION = quote(PANEL_VERSION, safe="")
PANEL_ASSET_QUERY = f"?v={PANEL_ASSET_VERSION}"
PANEL_MODULE_URL = f"{PANEL_MODULE_STATIC_URL}/panel.js{PANEL_ASSET_QUERY}"
PANEL_APP_URL = f"{PANEL_APP_STATIC_URL}/index.html{PANEL_ASSET_QUERY}"
PANEL_WEBCOMPONENT_NAME = _webcomponent_name_for_version(PANEL_VERSION)

_STATIC_PATHS_REGISTERED_KEY = f"{DOMAIN}_panel_static_paths_registered"
_PANEL_LOCK_KEY = f"{DOMAIN}_panel_registration_lock"
_PANEL_STARTUP_RECONCILE_KEY = f"{DOMAIN}_panel_startup_reconcile_registered"


def panel_is_registered(hass: HomeAssistant) -> bool:
    """Return whether Home Assistant currently exposes the FRAKON panel."""
    panels = hass.data.get(frontend.DATA_PANELS)
    return isinstance(panels, dict) and PANEL_URL_PATH in panels


def panel_registration_is_current(hass: HomeAssistant) -> bool:
    """Return whether the registered panel points at this installed release."""
    panels = hass.data.get(frontend.DATA_PANELS)
    if not isinstance(panels, dict):
        return False
    registered = panels.get(PANEL_URL_PATH)
    if registered is None:
        return False

    try:
        response = registered.to_response()
    except (AttributeError, TypeError):
        return False

    config = response.get("config")
    if not isinstance(config, dict):
        return False
    custom = config.get("_panel_custom")
    if not isinstance(custom, dict):
        return False
    return (
        custom.get("module_url") == PANEL_MODULE_URL
        and custom.get("name") == PANEL_WEBCOMPONENT_NAME
    )


def _registration_lock(hass: HomeAssistant) -> asyncio.Lock:
    lock = hass.data.get(_PANEL_LOCK_KEY)
    if isinstance(lock, asyncio.Lock):
        return lock
    lock = asyncio.Lock()
    hass.data[_PANEL_LOCK_KEY] = lock
    return lock


async def _async_register_panel_registry(hass: HomeAssistant) -> None:
    """Register the actual Home Assistant panel independently of asset routing."""
    if panel_is_registered(hass):
        if panel_registration_is_current(hass):
            return
        frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
        _LOGGER.info(
            "Replacing stale FRAKON Energy panel registration with release %s",
            PANEL_VERSION,
        )

    await panel_custom.async_register_panel(
        hass,
        webcomponent_name=PANEL_WEBCOMPONENT_NAME,
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

    _LOGGER.info(
        "FRAKON Energy sidebar panel registered with asset version %s as %s",
        PANEL_ASSET_VERSION,
        PANEL_WEBCOMPONENT_NAME,
    )


async def _async_register_static_assets(hass: HomeAssistant) -> None:
    """Register independent, non-overlapping static roots for FRAKON UI assets."""
    if hass.data.get(_STATIC_PATHS_REGISTERED_KEY):
        return

    frontend_dir = Path(__file__).parent / "frontend"
    app_dir = Path(__file__).parent / "frontend_app"

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(PANEL_MODULE_STATIC_URL, str(frontend_dir), False),
            StaticPathConfig(PANEL_APP_STATIC_URL, str(app_dir), False),
        ]
    )
    hass.data[_STATIC_PATHS_REGISTERED_KEY] = True
    _LOGGER.info("FRAKON Energy frontend static assets registered")


async def _async_ensure_panel(hass: HomeAssistant) -> None:
    """Ensure both the HA panel registry entry and its static assets exist.

    The panel itself is deliberately registered first. A problem in aiohttp
    static-path registration must never make the `/frakon-energy` route vanish;
    it may only make the panel show an asset-loading error, which is visible and
    diagnosable while VisionQ/HDO/sensors remain unaffected.
    """
    async with _registration_lock(hass):
        await _async_register_panel_registry(hass)
        await _async_register_static_assets(hass)


async def _async_reconcile_panel_after_start(
    hass: HomeAssistant, _event: Event | None = None
) -> None:
    """Repair a panel lost or left stale while Home Assistant finishes bootstrap."""
    hass.data.pop(_PANEL_STARTUP_RECONCILE_KEY, None)
    try:
        await _async_ensure_panel(hass)
    except Exception:
        _LOGGER.exception(
            "Unable to reconcile FRAKON Energy sidebar panel after Home Assistant startup"
        )


def _schedule_post_start_reconcile(hass: HomeAssistant) -> None:
    """Install exactly one post-start reconciliation while HA is booting."""
    if hass.state is CoreState.running:
        return
    if hass.data.get(_PANEL_STARTUP_RECONCILE_KEY):
        return

    hass.data[_PANEL_STARTUP_RECONCILE_KEY] = True

    async def _handle_started(event: Event) -> None:
        await _async_reconcile_panel_after_start(hass, event)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _handle_started)


async def async_register_panel(hass: HomeAssistant) -> None:
    """Register FRAKON UI without ever making energy providers depend on the UI.

    Config entries can load while Home Assistant is still constructing the
    frontend registry. We therefore register immediately and reconcile once
    again after Home Assistant has fully started. UI failures are logged but
    never take VisionQ/HDO/sensors down.
    """
    _schedule_post_start_reconcile(hass)
    try:
        await _async_ensure_panel(hass)
    except Exception:
        _LOGGER.exception("Unable to fully register FRAKON Energy sidebar panel")
