"""EUR/CZK exchange-rate support for FRAKON Energy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

CNB_DAILY_URL = "https://www.cnb.cz/en/financial_markets/foreign_exchange_market/exchange_rate_fixing/daily.txt"


@dataclass(frozen=True, slots=True)
class ExchangeRate:
    pair: str
    rate: float
    source: str
    fetched_at: datetime


def parse_cnb_eur_czk(text: str) -> float:
    """Parse EUR/CZK from the CNB daily exchange-rate text feed."""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("Country|") or line.startswith("Date|"):
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue
        _country, _currency, amount, code, rate = parts[:5]
        if code != "EUR":
            continue
        units = float(amount.replace(",", "."))
        quoted = float(rate.replace(",", "."))
        if units <= 0 or quoted <= 0:
            raise ValueError("invalid EUR exchange rate returned by CNB")
        return quoted / units
    raise ValueError("EUR exchange rate was not found in CNB response")


class EurCzkRateProvider:
    """Small cached provider for the official CNB EUR/CZK fixing."""

    def __init__(self, fetch_text: Callable[[str], Awaitable[str]], cache_for: timedelta = timedelta(hours=6)) -> None:
        self._fetch_text = fetch_text
        self._cache_for = cache_for
        self._cached: ExchangeRate | None = None

    async def async_get(self, *, now: datetime | None = None) -> ExchangeRate:
        now = now or datetime.now(timezone.utc)
        if self._cached is not None and now - self._cached.fetched_at < self._cache_for:
            return self._cached
        text = await self._fetch_text(CNB_DAILY_URL)
        rate = parse_cnb_eur_czk(text)
        self._cached = ExchangeRate(pair="EUR/CZK", rate=rate, source="CNB", fetched_at=now)
        return self._cached
