"""Runtime registry for durable stop lifecycle repositories."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_stop_lifecycle import (
    ExecutionStopLifecycleRepository,
    home_assistant_stop_lifecycle_repository,
)

_RUNTIME_REPOSITORIES_KEY = "load_execution_stop_lifecycle_repositories_by_entry"


def stop_lifecycle_repository(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionStopLifecycleRepository:
    """Return one stop lifecycle repository instance per config entry/process."""
    if not entry_id:
        raise ValueError("entry_id is required")
    domain_data = hass.data.setdefault(DOMAIN, {})
    repositories = domain_data.get(_RUNTIME_REPOSITORIES_KEY)
    if not isinstance(repositories, dict):
        repositories = {}
        domain_data[_RUNTIME_REPOSITORIES_KEY] = repositories
    repository = repositories.get(entry_id)
    if isinstance(repository, ExecutionStopLifecycleRepository):
        return repository
    repository = home_assistant_stop_lifecycle_repository(hass, entry_id)
    repositories[entry_id] = repository
    return repository
