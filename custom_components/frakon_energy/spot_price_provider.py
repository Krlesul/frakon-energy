"""Provider orchestration and cache for FRAKON Energy spot prices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Protocol

from .spot_price_model import SpotPriceSnapshot


class SpotPriceProvider(Protocol):
    """Contract implemented by concrete internet market-data providers."""

    name: str

    async def async_fetch(self, *, now: datetime) -> SpotPriceSnapshot:
        """Fetch and normalize the current day-ahead snapshot."""


@dataclass(slots=True)
class SpotPriceCache:
    """Last known good market snapshot."""

    snapshot: SpotPriceSnapshot | None = None
    refreshed_at: datetime | None = None

    def is_fresh(self, *, now: datetime, max_age: timedelta) -> bool:
        return (
            self.snapshot is not None
            and self.refreshed_at is not None
            and now - self.refreshed_at <= max_age
        )


@dataclass(frozen=True, slots=True)
class SpotPriceRuntimeResult:
    """Result exposed to the integration layer."""

    snapshot: SpotPriceSnapshot
    provider: str
    stale: bool
    fallback_used: bool
    error: str | None = None


class SpotPriceProviderRuntime:
    """Try providers in order and preserve the last known good snapshot."""

    def __init__(
        self,
        providers: tuple[SpotPriceProvider, ...],
        *,
        cache_max_age: timedelta = timedelta(minutes=15),
        stale_max_age: timedelta = timedelta(hours=36),
    ) -> None:
        if not providers:
            raise ValueError("at least one spot price provider is required")
        self._providers = providers
        self._cache_max_age = cache_max_age
        self._stale_max_age = stale_max_age
        self._cache = SpotPriceCache()

    async def async_get(self, *, now: datetime) -> SpotPriceRuntimeResult:
        if self._cache.is_fresh(now=now, max_age=self._cache_max_age):
            assert self._cache.snapshot is not None
            return SpotPriceRuntimeResult(
                snapshot=self._cache.snapshot,
                provider="cache",
                stale=False,
                fallback_used=False,
            )

        errors: list[str] = []
        for index, provider in enumerate(self._providers):
            try:
                snapshot = await provider.async_fetch(now=now)
            except Exception as err:  # provider boundary: continue to fallback
                errors.append(f"{provider.name}: {err}")
                continue

            self._cache.snapshot = snapshot
            self._cache.refreshed_at = now
            return SpotPriceRuntimeResult(
                snapshot=snapshot,
                provider=provider.name,
                stale=False,
                fallback_used=index > 0,
                error="; ".join(errors) or None,
            )

        if self._cache.is_fresh(now=now, max_age=self._stale_max_age):
            assert self._cache.snapshot is not None
            return SpotPriceRuntimeResult(
                snapshot=self._cache.snapshot,
                provider="cache",
                stale=True,
                fallback_used=True,
                error="; ".join(errors) or "all providers unavailable",
            )

        raise RuntimeError("spot price data unavailable: " + "; ".join(errors))
