"""Runtime registry for persistent execution action-snapshot repositories."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .load_execution_action_snapshot import (
    ExecutionActionSnapshotRepository,
    home_assistant_action_snapshot_repository,
)

_RUNTIME_REPOSITORIES_KEY = "load_execution_action_snapshot_repositories_by_entry"


def action_snapshot_repository(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionActionSnapshotRepository:
    """Return one repository instance per FRAKON Energy config entry/process."""
    if not entry_id:
        raise ValueError("entry_id is required")
    domain_data = hass.data.setdefault(DOMAIN, {})
    repositories = domain_data.get(_RUNTIME_REPOSITORIES_KEY)
    if not isinstance(repositories, dict):
        repositories = {}
        domain_data[_RUNTIME_REPOSITORIES_KEY] = repositories
    repository = repositories.get(entry_id)
    if isinstance(repository, ExecutionActionSnapshotRepository):
        return repository
    repository = home_assistant_action_snapshot_repository(hass, entry_id)
    repositories[entry_id] = repository
    return repository
