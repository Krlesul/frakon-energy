from datetime import datetime, timedelta, timezone

from custom_components.frakon_energy.spot_price_model import (
    SpotPriceInterval,
    SpotPriceSnapshot,
)


def test_spot_price_snapshot_orders_and_summarizes_intervals() -> None:
    start = datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc)
    intervals = [
        SpotPriceInterval(start + timedelta(hours=1), start + timedelta(hours=2), -10.0, "test"),
        SpotPriceInterval(start, start + timedelta(hours=1), 50.0, "test"),
    ]

    payload = SpotPriceSnapshot.from_intervals(
        market="CZ",
        currency="EUR",
        timezone="Europe/Prague",
        fetched_at=start,
        intervals=intervals,
    ).as_dict()

    assert payload["intervals"][0]["price_eur_mwh"] == 50.0
    assert payload["intervals"][0]["price_eur_kwh"] == 0.05
    assert payload["summary"]["minimum_eur_mwh"] == -10.0
    assert payload["summary"]["maximum_eur_mwh"] == 50.0
    assert payload["summary"]["average_eur_mwh"] == 20.0
    assert payload["summary"]["has_negative_price"] is True
