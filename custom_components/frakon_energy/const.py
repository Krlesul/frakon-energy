from __future__ import annotations

DOMAIN = "frakon_energy"

PLATFORMS: tuple[str, ...] = ("sensor",)

CONF_PROVIDER = "provider"
CONF_SCAN_INTERVAL = "scan_interval"

PROVIDER_VISIONQ = "visionq"

DEFAULT_SCAN_INTERVAL = 300
MIN_SCAN_INTERVAL = 60
MAX_SCAN_INTERVAL = 3600
