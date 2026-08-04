from __future__ import annotations

DOMAIN = "frakon_energy"
PLATFORMS: tuple[str, ...] = ("sensor",)

CONF_PROVIDER = "provider"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_EUI = "eui"

PROVIDER_VISIONQ = "visionq"

DEFAULT_SCAN_INTERVAL = 1800
MIN_SCAN_INTERVAL = 900
MAX_SCAN_INTERVAL = 86400
