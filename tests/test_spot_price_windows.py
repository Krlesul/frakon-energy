import pytest

from custom_components.frakon_energy.spot_price_windows import best_contiguous_window, optimization_payload


def _interval(index: int, price: float) -> dict[str, object]:
    return {
        "starts_at": f"2026-08-07T{index // 4:02d}:{(index % 4) * 15:02d}:00+02:00",
        "ends_at": f"2026-08-07T{(index + 1) // 4:02d}:{((index + 1) % 4) * 15:02d}:00+02:00",
        "price_czk_kwh": price,
    }


def test_finds_cheapest_one_hour_window() -> None:
    intervals = [_interval(index, 8.0) for index in range(12)]
    for index, price in enumerate([2.0, 1.0, -0.5, 1.5], start=4):
        intervals[index] = _interval(index, price)
    result = best_contiguous_window(intervals, interval_count=4)
    assert result is not None
    assert result["starts_at"] == intervals[4]["starts_at"]
    assert result["ends_at"] == intervals[7]["ends_at"]
    assert result["average_czk_kwh"] == pytest.approx(1.0)


def test_rejects_window_longer_than_available_data() -> None:
    assert best_contiguous_window([_interval(0, 1.0)], interval_count=4) is None


def test_optimization_payload_ranks_extremes_and_windows() -> None:
    intervals = [_interval(index, float(index)) for index in range(20)]
    payload = optimization_payload(intervals)
    assert [item["price_czk_kwh"] for item in payload["cheapest_intervals"]] == [0.0, 1.0, 2.0, 3.0]
    assert [item["price_czk_kwh"] for item in payload["most_expensive_intervals"]] == [19.0, 18.0, 17.0, 16.0]
    assert payload["cheapest_windows"]["1h"]["average_czk_kwh"] == pytest.approx(1.5)
    assert payload["cheapest_windows"]["4h"]["interval_count"] == 16
