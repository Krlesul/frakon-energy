from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True, slots=True)
class MeterSegment:
    """One physical meter used during part of a billing cycle."""

    valid_from: date
    start_high_rate_kwh: Decimal
    start_low_rate_kwh: Decimal
    valid_to: date | None = None
    end_high_rate_kwh: Decimal | None = None
    end_low_rate_kwh: Decimal | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("meter_end_before_start")
        if (self.end_high_rate_kwh is None) != (self.end_low_rate_kwh is None):
            raise ValueError("meter_end_readings_incomplete")
        if self.valid_to is not None and self.end_high_rate_kwh is None:
            raise ValueError("meter_end_readings_required")
        if self.end_high_rate_kwh is not None and self.end_high_rate_kwh < self.start_high_rate_kwh:
            raise ValueError("meter_vt_decreased")
        if self.end_low_rate_kwh is not None and self.end_low_rate_kwh < self.start_low_rate_kwh:
            raise ValueError("meter_nt_decreased")

    def consumption(
        self,
        *,
        current_high_rate_kwh: Decimal | None = None,
        current_low_rate_kwh: Decimal | None = None,
    ) -> tuple[Decimal, Decimal]:
        end_vt = self.end_high_rate_kwh if self.end_high_rate_kwh is not None else current_high_rate_kwh
        end_nt = self.end_low_rate_kwh if self.end_low_rate_kwh is not None else current_low_rate_kwh
        if end_vt is None or end_nt is None:
            raise ValueError("current_meter_readings_required")
        if end_vt < self.start_high_rate_kwh:
            raise ValueError("current_meter_vt_below_start")
        if end_nt < self.start_low_rate_kwh:
            raise ValueError("current_meter_nt_below_start")
        return end_vt - self.start_high_rate_kwh, end_nt - self.start_low_rate_kwh


def validate_meter_chain(segments: Iterable[MeterSegment], *, cycle_start: date, settlement_date: date) -> tuple[MeterSegment, ...]:
    ordered = tuple(sorted(segments, key=lambda item: item.valid_from))
    if not ordered:
        raise ValueError("meter_chain_empty")
    if ordered[0].valid_from != cycle_start:
        raise ValueError("first_meter_must_start_with_cycle")
    if ordered[-1].valid_to is not None:
        raise ValueError("last_meter_must_be_active")
    for index, segment in enumerate(ordered):
        if segment.valid_from < cycle_start or segment.valid_from > settlement_date:
            raise ValueError("meter_outside_cycle")
        if segment.valid_to is not None and segment.valid_to > settlement_date:
            raise ValueError("meter_outside_cycle")
        if index > 0:
            previous = ordered[index - 1]
            if previous.valid_to is None:
                raise ValueError("only_last_meter_can_be_active")
            if previous.valid_to != segment.valid_from:
                raise ValueError("meter_chain_has_gap_or_overlap")
    return ordered


def total_cycle_consumption(
    segments: Iterable[MeterSegment],
    *,
    cycle_start: date,
    settlement_date: date,
    current_high_rate_kwh: Decimal,
    current_low_rate_kwh: Decimal,
) -> tuple[Decimal, Decimal]:
    ordered = validate_meter_chain(segments, cycle_start=cycle_start, settlement_date=settlement_date)
    total_vt = Decimal("0")
    total_nt = Decimal("0")
    for index, segment in enumerate(ordered):
        vt, nt = segment.consumption(
            current_high_rate_kwh=current_high_rate_kwh if index == len(ordered) - 1 else None,
            current_low_rate_kwh=current_low_rate_kwh if index == len(ordered) - 1 else None,
        )
        total_vt += vt
        total_nt += nt
    return total_vt, total_nt
