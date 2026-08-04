from custom_components.frakon_energy.device_trigger import (
    TRIGGER_LOW_TARIFF_ENDED,
    TRIGGER_LOW_TARIFF_STARTED,
    TRIGGER_TYPES,
)


def test_hdo_device_trigger_types_are_stable():
    assert TRIGGER_TYPES == {
        TRIGGER_LOW_TARIFF_STARTED,
        TRIGGER_LOW_TARIFF_ENDED,
    }
    assert TRIGGER_LOW_TARIFF_STARTED == "low_tariff_started"
    assert TRIGGER_LOW_TARIFF_ENDED == "low_tariff_ended"
