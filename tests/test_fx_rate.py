from datetime import datetime, timedelta, timezone

import pytest

from custom_components.frakon_energy.fx_rate import EurCzkRateProvider, parse_cnb_eur_czk


CNB_SAMPLE = """07 Aug 2026 #152\nCountry|Currency|Amount|Code|Rate\nEMU|euro|1|EUR|24.385\nUnited States|dollar|1|USD|20.914\n"""


def test_parse_cnb_eur_czk() -> None:
    assert parse_cnb_eur_czk(CNB_SAMPLE) == pytest.approx(24.385)


def test_parse_respects_currency_amount() -> None:
    text = "Country|Currency|Amount|Code|Rate\nEMU|euro|100|EUR|2438.5\n"
    assert parse_cnb_eur_czk(text) == pytest.approx(24.385)


def test_missing_eur_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_cnb_eur_czk("Country|Currency|Amount|Code|Rate\nUSA|dollar|1|USD|20.9\n")

@pytest.mark.asyncio
async def test_provider_caches_rate() -> None:
    calls = 0
    async def fetch_text(_url: str) -> str:
        nonlocal calls
        calls += 1
        return CNB_SAMPLE
    provider = EurCzkRateProvider(fetch_text, cache_for=timedelta(hours=6))
    now = datetime(2026, 8, 7, 12, tzinfo=timezone.utc)
    first = await provider.async_get(now=now)
    second = await provider.async_get(now=now + timedelta(hours=1))
    assert first.rate == pytest.approx(24.385)
    assert second == first
    assert calls == 1
