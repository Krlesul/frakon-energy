from __future__ import annotations

from types import SimpleNamespace

from custom_components.frakon_energy.const import DOMAIN
from custom_components.frakon_energy.entry_runtime_cache import (
    purge_entry_scoped_domain_caches,
)


def test_purge_removes_only_target_entry_from_explicit_entry_caches() -> None:
    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "load_execution_lifecycle_repositories_by_entry": {
                    "entry-1": object(),
                    "entry-2": object(),
                },
                "load_execution_phase_settlement_runtimes_by_entry": {
                    "entry-1": object(),
                },
                "load_profiles_websocket_registered": True,
                "not_an_entry_cache": {"entry-1": object()},
                "scalar_by_entry": "not-a-dict",
            }
        }
    )

    purged = purge_entry_scoped_domain_caches(hass, "entry-1")

    assert purged == (
        "load_execution_lifecycle_repositories_by_entry",
        "load_execution_phase_settlement_runtimes_by_entry",
    )
    assert "entry-1" not in hass.data[DOMAIN]["load_execution_lifecycle_repositories_by_entry"]
    assert "entry-2" in hass.data[DOMAIN]["load_execution_lifecycle_repositories_by_entry"]
    assert hass.data[DOMAIN]["load_profiles_websocket_registered"] is True
    assert "entry-1" in hass.data[DOMAIN]["not_an_entry_cache"]
    assert hass.data[DOMAIN]["scalar_by_entry"] == "not-a-dict"


def test_purge_without_domain_data_is_safe() -> None:
    hass = SimpleNamespace(data={})
    assert purge_entry_scoped_domain_caches(hass, "entry-1") == ()
