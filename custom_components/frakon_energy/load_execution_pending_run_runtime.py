"""Runtime registry for FRAKON Energy durable pending-run repositories."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_pending_run import (
    ExecutionPendingRunRepository,
    home_assistant_pending_run_repository,
)

_RUNTIME_KEY = "load_execution_pending_run_repositories_by_entry"


def pending_run_repository(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionPendingRunRepository:
    if not entry_id:
        raise ValueError("entry_id is required")
    domain_data = hass.data.setdefault(DOMAIN, {})
    repositories = domain_data.get(_RUNTIME_KEY)
    if not isinstance(repositories, dict):
        repositories = {}
        domain_data[_RUNTIME_KEY] = repositories
    repository = repositories.get(entry_id)
    if isinstance(repository, ExecutionPendingRunRepository):
        return repository
    repository = home_assistant_pending_run_repository(hass, entry_id)
    repositories[entry_id] = repository
    return repository
