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


def test_day_ahead_payload_splits_market_local_today_and_tomorrow() -> None:
    now = datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)
    today_start = datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc)
    tomorrow_start = datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc)
    snapshot = SpotPriceSnapshot.from_intervals(
        market="CZ",
        currency="EUR",
        timezone="Europe/Prague",
        fetched_at=now,
        intervals=(
            SpotPriceInterval(today_start, today_start + timedelta(hours=1), 25.0, "test"),
            SpotPriceInterval(tomorrow_start, tomorrow_start + timedelta(hours=1), -5.0, "test"),
        ),
    )

    payload = snapshot.day_ahead_payload(now=now)

    assert payload["today"]["date"] == "2026-08-07"
    assert payload["today"]["available"] is True
    assert payload["today"]["interval_count"] == 1
    assert payload["tomorrow"]["date"] == "2026-08-08"
    assert payload["tomorrow"]["available"] is True
    assert payload["tomorrow"]["has_negative_price"] is True
