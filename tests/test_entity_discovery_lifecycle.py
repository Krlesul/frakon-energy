import pytest

from custom_components.frakon_energy.entity_discovery_lifecycle import (
    EntityDiscoveryRuntimeRegistry,
)


class DummyRuntime:
    pass


def test_register_get_remove_runtime() -> None:
    registry = EntityDiscoveryRuntimeRegistry()
    runtime = DummyRuntime()

    registry.register("entry-1", runtime)  # type: ignore[arg-type]

    assert registry.get("entry-1") is runtime
    assert registry.as_frontend_summary() == {
        "entry_ids": ["entry-1"],
        "count": 1,
    }
    assert registry.remove("entry-1") is runtime
    assert registry.as_frontend_summary()["count"] == 0


def test_missing_runtime_raises_clear_error() -> None:
    registry = EntityDiscoveryRuntimeRegistry()

    with pytest.raises(KeyError, match="entry-404"):
        registry.get("entry-404")


def test_empty_entry_id_is_rejected() -> None:
    registry = EntityDiscoveryRuntimeRegistry()

    with pytest.raises(ValueError, match="entry_id"):
        registry.register("", DummyRuntime())  # type: ignore[arg-type]
