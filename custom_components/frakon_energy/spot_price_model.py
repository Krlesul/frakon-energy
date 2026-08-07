"""Provider-neutral spot-price data model for FRAKON Energy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class SpotPriceInterval:
    """One market-price interval in local market time."""

    starts_at: datetime
    ends_at: datetime
    price_eur_mwh: float
    source: str

    @property
    def price_eur_kwh(self) -> float:
        return self.price_eur_mwh / 1000.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "starts_at": self.starts_at.isoformat(),
            "ends_at": self.ends_at.isoformat(),
            "price_eur_mwh": self.price_eur_mwh,
            "price_eur_kwh": self.price_eur_kwh,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class SpotPriceSnapshot:
    """Normalized current day-ahead market snapshot."""

    market: str
    currency: str
    timezone: str
    fetched_at: datetime
    intervals: tuple[SpotPriceInterval, ...]

    @classmethod
    def from_intervals(
        cls,
        *,
        market: str,
        currency: str,
        timezone: str,
        fetched_at: datetime,
        intervals: Iterable[SpotPriceInterval],
    ) -> "SpotPriceSnapshot":
        ordered = tuple(sorted(intervals, key=lambda item: item.starts_at))
        return cls(
            market=market,
            currency=currency,
            timezone=timezone,
            fetched_at=fetched_at,
            intervals=ordered,
        )

    def as_dict(self) -> dict[str, Any]:
        prices = [item.price_eur_mwh for item in self.intervals]
        return {
            "market": self.market,
            "currency": self.currency,
            "timezone": self.timezone,
            "fetched_at": self.fetched_at.isoformat(),
            "intervals": [item.as_dict() for item in self.intervals],
            "summary": {
                "count": len(prices),
                "minimum_eur_mwh": min(prices) if prices else None,
                "maximum_eur_mwh": max(prices) if prices else None,
                "average_eur_mwh": sum(prices) / len(prices) if prices else None,
                "has_negative_price": any(price < 0 for price in prices),
            },
        }
