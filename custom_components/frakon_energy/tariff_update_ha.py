"""Home Assistant adapter for durable active tariff source checks."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .tariff_http_transport import DEFAULT_TARIFF_HTTP_TIMEOUT_SECONDS
from .tariff_update_cadence import (
    DEFAULT_TARIFF_UPDATE_INTERVAL,
    active_tariff_check_cadence,
)
from .tariff_update_notifications import (
    notification_for_new_pending_tariff,
    pending_tariff_hashes,
)
from .tariff_update_orchestrator import (
    TariffUpdateCheckRun,
    async_check_active_tariff_source,
)

_NOTIFICATION_PREFIX = f"{DOMAIN}_tariff_update"


async def async_check_active_tariff_source_ha(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    day: date,
    checked_at: datetime,
    timeout_seconds: float = DEFAULT_TARIFF_HTTP_TIMEOUT_SECONDS,
) -> TariffUpdateCheckRun:
    """Run one active tariff source check and persist only its durable options state.

    The pure orchestrator owns all contract/catalog/watch safety rules. This HA
    adapter only provides Home Assistant's shared HTTP session and writes the
    returned options snapshot when it actually differs from the config entry.
    It never confirms or activates a tariff and never parses changed PDF bytes.
    """
    if not isinstance(day, date):
        raise ValueError("day must be a date")
    if not isinstance(checked_at, datetime) or checked_at.tzinfo is None:
        raise ValueError("checked_at must be a timezone-aware datetime")

    session = async_get_clientsession(hass)
    run = await async_check_active_tariff_source(
        entry.options,
        day=day,
        session=session,
        checked_at=checked_at,
        timeout_seconds=timeout_seconds,
    )
    if dict(entry.options) != run.updated_options:
        hass.config_entries.async_update_entry(
            entry,
            options=run.updated_options,
        )
    return run


def _notify_if_new_pending(
    hass: HomeAssistant,
    entry: ConfigEntry,
    run: TariffUpdateCheckRun,
    *,
    pending_before: dict[str, str | None],
) -> bool:
    notification = notification_for_new_pending_tariff(
        run,
        pending_before=pending_before,
    )
    if notification is None:
        return False
    persistent_notification.async_create(
        hass,
        notification.message,
        title=notification.title,
        notification_id=f"{_NOTIFICATION_PREFIX}_{entry.entry_id}",
    )
    return True


async def async_check_active_tariff_source_if_due_ha(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    day: date,
    checked_at: datetime,
    interval: timedelta = DEFAULT_TARIFF_UPDATE_INTERVAL,
    timeout_seconds: float = DEFAULT_TARIFF_HTTP_TIMEOUT_SECONDS,
) -> TariffUpdateCheckRun | None:
    """Run the active tariff source check only when its durable cadence is due.

    A newly authorized source is checked immediately. Once a durable last-check
    timestamp exists, no network request is made before the full cadence interval
    has elapsed. A user notification is emitted only when the due check creates a
    genuinely new pending document hash; repeated checks of the same pending hash
    are silent and active pricing is never changed here.
    """
    cadence = active_tariff_check_cadence(
        entry.options,
        day=day,
        checked_at=checked_at,
        interval=interval,
    )
    if not cadence.due:
        return None

    pending_before = pending_tariff_hashes(dict(entry.options))
    run = await async_check_active_tariff_source_ha(
        hass,
        entry,
        day=day,
        checked_at=checked_at,
        timeout_seconds=timeout_seconds,
    )
    _notify_if_new_pending(
        hass,
        entry,
        run,
        pending_before=pending_before,
    )
    return run
