"""OTE Czech day-ahead market adapter primitives.

The HTTP transport is injected so Home Assistant can provide its managed aiohttp
session without coupling parsing and tests to network access.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from .spot_price_model import SpotPriceInterval, SpotPriceSnapshot

OTE_DAM_URL = "https://www.ote-cr.cz/en/short-term-markets/electricity/day-ahead-market"


class _DamTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_cell = False
        self._cell_parts: list[str] = []
        self._row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"td", "th"}:
            self._in_cell = True
            self._cell_parts = []
        elif tag == "tr":
            self._row = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._in_cell:
            self._row.append(" ".join("".join(self._cell_parts).split()))
            self._in_cell = False
        elif tag == "tr" and self._row:
            self.rows.append(self._row)
            self._row = []


def _parse_decimal(value: str) -> float:
    normalized = value.replace("\xa0", " ").replace(" ", "").replace(",", ".")
    return float(normalized)


def parse_ote_dam_html(*, html: str, market_date: date) -> tuple[SpotPriceInterval, ...]:
    """Parse OTE's published 15-minute DAM table into normalized intervals."""
    parser = _DamTableParser()
    parser.feed(html)
    timezone = ZoneInfo("Europe/Prague")
    intervals: list[SpotPriceInterval] = []

    for row in parser.rows:
        if len(row) < 2 or "-" not in row[0]:
            continue
        start_text, end_text = (part.strip() for part in row[0].split("-", 1))
        try:
            start_time = datetime.strptime(start_text, "%H:%M").time()
            end_time = datetime.strptime(end_text, "%H:%M").time()
            price = _parse_decimal(row[1])
        except ValueError:
            continue

        starts_at = datetime.combine(market_date, start_time, timezone)
        ends_at = datetime.combine(market_date, end_time, timezone)
        if ends_at <= starts_at:
            ends_at += timedelta(days=1)
        intervals.append(
            SpotPriceInterval(
                starts_at=starts_at,
                ends_at=ends_at,
                price_eur_mwh=price,
                source="OTE DAM",
            )
        )

    if not intervals:
        raise ValueError("OTE DAM response contained no price intervals")
    return tuple(intervals)


class OteSpotPriceProvider:
    """Fetch Czech day-ahead prices from OTE public market results."""

    name = "OTE"

    def __init__(self, fetch_text: Callable[[str], Awaitable[str]]) -> None:
        self._fetch_text = fetch_text

    async def async_fetch(self, *, now: datetime) -> SpotPriceSnapshot:
        market_tz = ZoneInfo("Europe/Prague")
        local_today = now.astimezone(market_tz).date()
        dates = (local_today, local_today + timedelta(days=1))
        intervals: list[SpotPriceInterval] = []

        for market_date in dates:
            url = f"{OTE_DAM_URL}?date={market_date.isoformat()}&set_language=en&time_resolution=PT15M"
            html = await self._fetch_text(url)
            try:
                intervals.extend(parse_ote_dam_html(html=html, market_date=market_date))
            except ValueError:
                # Tomorrow is legitimately unavailable before the day-ahead result is published.
                if market_date == local_today:
                    raise

        return SpotPriceSnapshot.from_intervals(
            market="CZ",
            currency="EUR",
            timezone="Europe/Prague",
            fetched_at=now,
            intervals=intervals,
        )
