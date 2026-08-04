from __future__ import annotations

DOMAIN = "frakon_energy"
PLATFORMS: tuple[str, ...] = ("sensor",)

CONF_PROVIDER = "provider"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_EUI = "eui"
CONF_HDO_SOURCE_ID = "hdo_source_id"
CONF_HDO_SCHEDULE_ENTITY = "hdo_schedule_entity"
CONF_HDO_LOW_TARIFF_ENTITY = "hdo_low_tariff_entity"
CONF_HDO_CURRENT_PRICE_ENTITY = "hdo_current_price_entity"
CONF_HDO_DATA_VALID_ENTITY = "hdo_data_valid_entity"

PROVIDER_VISIONQ = "visionq"
PROVIDER_CEZ_HDO = "cez_hdo"

EVENT_TARIFF_CHANGED = "frakon_energy_tariff_changed"

DEFAULT_SCAN_INTERVAL = 1800
MIN_SCAN_INTERVAL = 900
MAX_SCAN_INTERVAL = 86400
HDO_UPDATE_INTERVAL = 1
