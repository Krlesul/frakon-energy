from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components import frakon_energy
from custom_components.frakon_energy.const import DOMAIN
from custom_components.frakon_energy.entity_discovery_lifecycle import (
    EntityDiscoveryRuntimeRegistry,
)


class _ConfigEntries:
    def __init__(self) -> None:
        self.unloads: list[tuple[str, tuple[str, ...]]] = []
        self.fail_unload = False

    async def async_unload_platforms(self, entry, platforms):
        self.unloads.append((entry.entry_id, tuple(platforms)))
        if self.fail_unload:
            raise RuntimeError("platform unload failed")
        return True


class _Hass:
    def __init__(self) -> None:
        self.data: dict = {DOMAIN: {}}
        self.config_entries = _ConfigEntries()


def _registry_with_entry(entry_id: str) -> EntityDiscoveryRuntimeRegistry:
    registry = EntityDiscoveryRuntimeRegistry()
    registry.register(entry_id, object())  # type: ignore[arg-type]
    return registry


@pytest.mark.asyncio
async def test_failed_setup_cleanup_removes_forwarded_platform_runtime_and_coordinator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    entry = SimpleNamespace(entry_id="entry-1")
    registry = _registry_with_entry(entry.entry_id)
    hass.data[DOMAIN][entry.entry_id] = object()
    stop_calls: list[str] = []

    async def stop_runtimes(hass_arg, entry_id: str) -> None:
        stop_calls.append(entry_id)

    monkeypatch.setattr(frakon_energy, "async_stop_execution_runtimes", stop_runtimes)

    await frakon_energy._async_rollback_failed_setup(
        hass,  # type: ignore[arg-type]
        entry,  # type: ignore[arg-type]
        runtime_registry=registry,
        discovery_registered=True,
        sensors_forwarded=True,
    )

    assert hass.config_entries.unloads == [("entry-1", ("sensor",))]
    assert stop_calls == ["entry-1"]
    with pytest.raises(KeyError):
        registry.get("entry-1")
    assert "entry-1" not in hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_early_failed_setup_does_not_unload_unforwarded_platform_and_cleanup_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    entry = SimpleNamespace(entry_id="entry-1")
    registry = _registry_with_entry(entry.entry_id)
    hass.data[DOMAIN][entry.entry_id] = object()

    async def fail_stop(hass_arg, entry_id: str) -> None:
        raise RuntimeError("runtime stop failed")

    monkeypatch.setattr(frakon_energy, "async_stop_execution_runtimes", fail_stop)

    await frakon_energy._async_rollback_failed_setup(
        hass,  # type: ignore[arg-type]
        entry,  # type: ignore[arg-type]
        runtime_registry=registry,
        discovery_registered=True,
        sensors_forwarded=False,
    )

    assert hass.config_entries.unloads == []
    with pytest.raises(KeyError):
        registry.get("entry-1")
    assert "entry-1" not in hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_platform_unload_failure_does_not_prevent_other_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    hass.config_entries.fail_unload = True
    entry = SimpleNamespace(entry_id="entry-1")
    registry = _registry_with_entry(entry.entry_id)
    hass.data[DOMAIN][entry.entry_id] = object()
    stop_calls: list[str] = []

    async def stop_runtimes(hass_arg, entry_id: str) -> None:
        stop_calls.append(entry_id)

    monkeypatch.setattr(frakon_energy, "async_stop_execution_runtimes", stop_runtimes)

    await frakon_energy._async_rollback_failed_setup(
        hass,  # type: ignore[arg-type]
        entry,  # type: ignore[arg-type]
        runtime_registry=registry,
        discovery_registered=True,
        sensors_forwarded=True,
    )

    assert stop_calls == ["entry-1"]
    with pytest.raises(KeyError):
        registry.get("entry-1")
    assert "entry-1" not in hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_unload_cleanup_removes_discovery_and_coordinator_after_runtime_stop_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    entry = SimpleNamespace(entry_id="entry-1")
    registry = _registry_with_entry(entry.entry_id)
    hass.data[DOMAIN][entry.entry_id] = object()

    async def fail_stop(hass_arg, entry_id: str) -> None:
        raise RuntimeError("runtime stop failed")

    monkeypatch.setattr(frakon_energy, "async_stop_execution_runtimes", fail_stop)

    with pytest.raises(RuntimeError, match="runtime stop failed"):
        await frakon_energy._async_cleanup_unloaded_entry(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            runtime_registry=registry,
        )

    with pytest.raises(KeyError):
        registry.get("entry-1")
    assert "entry-1" not in hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_unload_cleanup_preserves_first_error_but_still_removes_coordinator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    entry = SimpleNamespace(entry_id="entry-1")
    registry = _registry_with_entry(entry.entry_id)
    hass.data[DOMAIN][entry.entry_id] = object()

    async def fail_stop(hass_arg, entry_id: str) -> None:
        raise RuntimeError("first runtime error")

    def fail_discovery(*, entry_id: str, runtime_registry) -> bool:
        raise RuntimeError("discovery cleanup error")

    monkeypatch.setattr(frakon_energy, "async_stop_execution_runtimes", fail_stop)
    monkeypatch.setattr(frakon_energy, "unload_entity_discovery_runtime", fail_discovery)

    with pytest.raises(RuntimeError, match="first runtime error"):
        await frakon_energy._async_cleanup_unloaded_entry(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            runtime_registry=registry,
        )

    assert "entry-1" not in hass.data[DOMAIN]
