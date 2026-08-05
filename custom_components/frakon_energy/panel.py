from __future__ import annotations

from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

PANEL_URL_PATH = "frakon-energy"
PANEL_STATIC_URL = "/frakon-energy-static"
PANEL_MODULE_URL = f"{PANEL_STATIC_URL}/panel.js"
PANEL_TITLE = "FRAKON Energy"
PANEL_ICON = "mdi:lightning-bolt-circle"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Register FRAKON Energy static assets and sidebar panel once."""
    marker = f"{DOMAIN}_panel_registered"
    if hass.data.get(marker):
        return

    frontend_dir = Path(__file__).parent / "frontend"
    app_dir = Path(__file__).parent / "frontend_app"

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

    # The wrapper is a Home Assistant custom element. It embeds the React app in
    # a same-origin iframe and forwards the live `hass` object to it.
    from homeassistant.components import panel_custom

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
    hass.data[marker] = True
