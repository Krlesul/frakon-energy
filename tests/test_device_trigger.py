import pytest
import voluptuous as vol

from custom_components.frakon_energy.const import DOMAIN
from custom_components.frakon_energy.device_trigger import (
    TRIGGER_LOW_TARIFF_ENDED,
    TRIGGER_LOW_TARIFF_STARTED,
    TRIGGER_SCHEMA,
    TRIGGER_TYPES,
)


def test_hdo_device_trigger_types_are_stable():
    assert TRIGGER_TYPES == {
        TRIGGER_LOW_TARIFF_STARTED,
        TRIGGER_LOW_TARIFF_ENDED,
    }
    assert TRIGGER_LOW_TARIFF_STARTED == "low_tariff_started"
    assert TRIGGER_LOW_TARIFF_ENDED == "low_tariff_ended"


def test_hdo_device_trigger_schema_accepts_current_device_contract():
    config = TRIGGER_SCHEMA(
        {
            "platform": "device",
            "domain": DOMAIN,
            "device_id": "device-1",
            "type": TRIGGER_LOW_TARIFF_STARTED,
        }
    )
    assert config["platform"] == "device"
    assert config["domain"] == DOMAIN
    assert config["device_id"] == "device-1"


def test_hdo_device_trigger_schema_rejects_wrong_domain():
    with pytest.raises(vol.Invalid):
        TRIGGER_SCHEMA(
            {
                "platform": "device",
                "domain": "wrong_domain",
                "device_id": "device-1",
                "type": TRIGGER_LOW_TARIFF_STARTED,
            }
        )
