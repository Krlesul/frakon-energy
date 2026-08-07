"""Runtime registry for durable FRAKON Energy stop-lease repositories."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_stop_lease import (
    ExecutionStopLeaseRepository,
    home_assistant_stop_lease_repository,
)

_RUNTIME_KEY = "load_execution_stop_lease_repositories_by_entry"


def stop_lease_repository(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionStopLeaseRepository:
    """Return one stop-lease repository per config entry/process."""
    if not entry_id:
        raise ValueError("entry_id is required")
    domain_data = hass.data.setdefault(DOMAIN, {})
    repositories = domain_data.get(_RUNTIME_KEY)
    if not isinstance(repositories, dict):
        repositories = {}
        domain_data[_RUNTIME_KEY] = repositories
    repository = repositories.get(entry_id)
    if isinstance(repository, ExecutionStopLeaseRepository):
        return repository
    repository = home_assistant_stop_lease_repository(hass, entry_id)
    repositories[entry_id] = repository
    return repository
