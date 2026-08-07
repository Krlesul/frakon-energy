from datetime import date, datetime, timezone

import pytest

from custom_components.frakon_energy.spot_price_ote import (
    OteSpotPriceProvider,
    parse_ote_dam_html,
)


HTML = """
<table>
<tr><th>Time interval</th><th>15min price (EUR/MWh)</th><th>Volume</th></tr>
<tr><td>00:00-00:15</td><td>168,41</td><td>1 073,000</td></tr>
<tr><td>00:15-00:30</td><td>-10,25</td><td>1 086,250</td></tr>
</table>
"""


def test_parse_ote_dam_html_reads_quarter_hour_prices() -> None:
    intervals = parse_ote_dam_html(html=HTML, market_date=date(2026, 8, 7))

    assert len(intervals) == 2
    assert intervals[0].price_eur_mwh == 168.41
    assert intervals[0].starts_at.isoformat() == "2026-08-07T00:00:00+02:00"
    assert intervals[0].ends_at.isoformat() == "2026-08-07T00:15:00+02:00"
    assert intervals[1].price_eur_mwh == -10.25


@pytest.mark.asyncio
async def test_provider_keeps_today_when_tomorrow_is_not_published() -> None:
    calls: list[str] = []

    async def fetch_text(url: str) -> str:
        calls.append(url)
        return HTML if "2026-08-07" in url else "<html>No results yet</html>"

    provider = OteSpotPriceProvider(fetch_text)
    snapshot = await provider.async_fetch(
        now=datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)
    )
    payload = snapshot.day_ahead_payload(
        now=datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)
    )

    assert len(calls) == 2
    assert payload["today"]["available"] is True
    assert payload["tomorrow"]["available"] is False
