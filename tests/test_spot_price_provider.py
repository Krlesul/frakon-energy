from datetime import datetime, timedelta, timezone

import pytest

from custom_components.frakon_energy.spot_price_model import SpotPriceSnapshot
from custom_components.frakon_energy.spot_price_provider import SpotPriceProviderRuntime


class FakeProvider:
    def __init__(self, name: str, snapshot: SpotPriceSnapshot | None = None, error: Exception | None = None):
        self.name = name
        self.snapshot = snapshot
        self.error = error
        self.calls = 0

    async def async_fetch(self, *, now: datetime) -> SpotPriceSnapshot:
        self.calls += 1
        if self.error:
            raise self.error
        assert self.snapshot is not None
        return self.snapshot


def snapshot(now: datetime) -> SpotPriceSnapshot:
    return SpotPriceSnapshot.from_intervals(
        market="CZ",
        currency="EUR",
        timezone="Europe/Prague",
        fetched_at=now,
        intervals=(),
    )


@pytest.mark.asyncio
async def test_runtime_uses_fallback_provider() -> None:
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    primary = FakeProvider("primary", error=RuntimeError("offline"))
    fallback = FakeProvider("fallback", snapshot(now))
    runtime = SpotPriceProviderRuntime((primary, fallback))

    result = await runtime.async_get(now=now)

    assert result.provider == "fallback"
    assert result.fallback_used is True
    assert "primary: offline" in (result.error or "")


@pytest.mark.asyncio
async def test_runtime_reuses_fresh_cache() -> None:
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    provider = FakeProvider("primary", snapshot(now))
    runtime = SpotPriceProviderRuntime((provider,), cache_max_age=timedelta(minutes=15))

    await runtime.async_get(now=now)
    result = await runtime.async_get(now=now + timedelta(minutes=5))

    assert result.provider == "cache"
    assert result.stale is False
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_runtime_serves_recent_stale_cache_during_outage() -> None:
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    provider = FakeProvider("primary", snapshot(now))
    runtime = SpotPriceProviderRuntime(
        (provider,),
        cache_max_age=timedelta(minutes=1),
        stale_max_age=timedelta(hours=36),
    )
    await runtime.async_get(now=now)
    provider.error = RuntimeError("offline")
    provider.snapshot = None

    result = await runtime.async_get(now=now + timedelta(minutes=2))

    assert result.provider == "cache"
    assert result.stale is True
    assert "offline" in (result.error or "")
