from datetime import date
from decimal import Decimal

import pytest

from custom_components.frakon_energy.metering import MeterSegment, total_cycle_consumption


def test_total_consumption_across_meter_replacement() -> None:
    segments = (
        MeterSegment(
            valid_from=date(2026, 1, 27),
            valid_to=date(2026, 7, 10),
            start_high_rate_kwh=Decimal("0"),
            start_low_rate_kwh=Decimal("0"),
            end_high_rate_kwh=Decimal("2022"),
            end_low_rate_kwh=Decimal("1526"),
        ),
        MeterSegment(
            valid_from=date(2026, 7, 10),
            start_high_rate_kwh=Decimal("0"),
            start_low_rate_kwh=Decimal("0"),
        ),
    )

    vt, nt = total_cycle_consumption(
        segments,
        cycle_start=date(2026, 1, 27),
        settlement_date=date(2027, 1, 31),
        current_high_rate_kwh=Decimal("341.465"),
        current_low_rate_kwh=Decimal("324.931"),
    )

    assert vt == Decimal("2363.465")
    assert nt == Decimal("1850.931")


def test_replacement_chain_requires_matching_date() -> None:
    segments = (
        MeterSegment(
            valid_from=date(2026, 1, 27),
            valid_to=date(2026, 7, 10),
            start_high_rate_kwh=Decimal("0"),
            start_low_rate_kwh=Decimal("0"),
            end_high_rate_kwh=Decimal("100"),
            end_low_rate_kwh=Decimal("100"),
        ),
        MeterSegment(
            valid_from=date(2026, 7, 11),
            start_high_rate_kwh=Decimal("0"),
            start_low_rate_kwh=Decimal("0"),
        ),
    )

    with pytest.raises(ValueError, match="meter_chain_has_gap_or_overlap"):
        total_cycle_consumption(
            segments,
            cycle_start=date(2026, 1, 27),
            settlement_date=date(2027, 1, 31),
            current_high_rate_kwh=Decimal("10"),
            current_low_rate_kwh=Decimal("10"),
        )
