"""Optimization helpers for FRAKON Energy spot-price intervals."""

from __future__ import annotations

from typing import Any


def ranked_intervals(intervals: list[dict[str, Any]], *, limit: int = 4, reverse: bool = False) -> list[dict[str, Any]]:
    """Return the cheapest or most expensive individual intervals."""
    valid = [item for item in intervals if item.get("price_czk_kwh") is not None]
    ranked = sorted(valid, key=lambda item: float(item["price_czk_kwh"]), reverse=reverse)
    return ranked[:limit]


def best_contiguous_window(intervals: list[dict[str, Any]], *, interval_count: int) -> dict[str, Any] | None:
    """Find the cheapest contiguous window of a requested number of intervals."""
    if interval_count <= 0 or len(intervals) < interval_count:
        return None
    best: dict[str, Any] | None = None
    for start in range(0, len(intervals) - interval_count + 1):
        window = intervals[start : start + interval_count]
        if any(item.get("price_czk_kwh") is None for item in window):
            continue
        prices = [float(item["price_czk_kwh"]) for item in window]
        candidate = {
            "starts_at": window[0]["starts_at"],
            "ends_at": window[-1]["ends_at"],
            "interval_count": interval_count,
            "average_czk_kwh": sum(prices) / interval_count,
            "minimum_czk_kwh": min(prices),
            "maximum_czk_kwh": max(prices),
        }
        if best is None or candidate["average_czk_kwh"] < best["average_czk_kwh"]:
            best = candidate
    return best


def optimization_payload(intervals: list[dict[str, Any]]) -> dict[str, Any]:
    """Build customer-facing optimization hints from 15-minute prices."""
    return {
        "cheapest_intervals": ranked_intervals(intervals, limit=4),
        "most_expensive_intervals": ranked_intervals(intervals, limit=4, reverse=True),
        "cheapest_windows": {
            "1h": best_contiguous_window(intervals, interval_count=4),
            "2h": best_contiguous_window(intervals, interval_count=8),
            "3h": best_contiguous_window(intervals, interval_count=12),
            "4h": best_contiguous_window(intervals, interval_count=16),
        },
    }
