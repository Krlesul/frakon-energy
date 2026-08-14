"""Home Assistant runtime scheduler for durable tariff source update checks."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN
from .tariff_source_watch import STATUS_CHANGE_DETECTED, tariff_source_watch_fingerprint
from .tariff_source_watch_store import tariff_source_watch_records_from_options
from .tariff_update_ha import async_check_active_tariff_source_if_due_ha
from .tariff_update_orchestrator import TariffUpdateCheckRun

_LOGGER = logging.getLogger(__name__)

# The durable cadence gate remains exactly seven days. The runtime wakes more
# often so a Home Assistant restart cannot delay a due weekly check by another
# full week. Waking does not imply network access; the due adapter returns None
# before acquiring a session when the durable seven-day boundary is not reached.
TARIFF_UPDATE_WAKE_INTERVAL = timedelta(hours=1)
_NOTIFICATION_PREFIX = f"{DOMAIN}_tariff_update"


@dataclass(frozen=True, slots=True)
class ScheduledTariffUpdateResult:
    """One scheduler wake result plus its user-notification decision."""

    run: TariffUpdateCheckRun | None
    notification_created: bool = False

    def __post_init__(self) -> None:
        if self.run is not None and not isinstance(self.run, TariffUpdateCheckRun):
            raise ValueError("run must be TariffUpdateCheckRun or None")
        if not isinstance(self.notification_created, bool):
            raise ValueError("notification_created must be boolean")
        if self.run is not None and self.run.activation_performed is not False:
            raise ValueError("scheduled tariff update must never activate pricing")
        if self.run is None and self.notification_created:
            raise ValueError("skipped cadence cannot create a tariff notification")

    @property
    def check_performed(self) -> bool:
        return self.run is not None


def _pending_by_watch(options: Mapping[str, object]) -> dict[str, str | None]:
    return {
        tariff_source_watch_fingerprint(record.watch): record.pending_sha256
        for record in tariff_source_watch_records_from_options(options)
    }


def _notification_id(entry: ConfigEntry) -> str:
    return f"{_NOTIFICATION_PREFIX}_{entry.entry_id}"


def _notify_new_pending_tariff(
    hass: HomeAssistant,
    entry: ConfigEntry,
    run: TariffUpdateCheckRun,
) -> None:
    watch = run.prepared.record.watch
    observed = run.check.observed_sha256
    if observed is None:
        raise ValueError("change notification requires observed checksum")

    persistent_notification.async_create(
        hass,
        (
            f"A newer official tariff document was detected for **{watch.product_name}** "
            f"from **{watch.source_name}**. The active tariff has not changed and "
            "will remain unchanged until the new version is reviewed and confirmed.\n\n"
            f"Source: [{watch.document_name}]({watch.source_url})\n\n"
            f"Detected document SHA-256: `{observed}`"
        ),
        title="FRAKON Energy: tariff update available",
        notification_id=_notification_id(entry),
    )


async def async_run_scheduled_tariff_update(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    now: datetime,
) -> ScheduledTariffUpdateResult:
    """Run one scheduler wake and notify only for a newly pending document."""
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime")

    pending_before = _pending_by_watch(dict(entry.options))
    checked_at = now.astimezone(timezone.utc)
    run = await async_check_active_tariff_source_if_due_ha(
        hass,
        entry,
        day=now.date(),
        checked_at=checked_at,
    )
    if run is None:
        return ScheduledTariffUpdateResult(run=None)

    notification_created = False
    observed = run.check.observed_sha256
    if (
        run.check.status == STATUS_CHANGE_DETECTED
        and run.check.requires_confirmation
        and observed is not None
        and pending_before.get(run.check.watch_fingerprint) != observed
    ):
        _notify_new_pending_tariff(hass, entry, run)
        notification_created = True

    return ScheduledTariffUpdateResult(
        run=run,
        notification_created=notification_created,
    )


def async_start_tariff_update_scheduler(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Register one non-overlapping runtime wake loop for a config entry."""
    lock = asyncio.Lock()

    async def _scheduled(now: datetime) -> None:
        if lock.locked():
            _LOGGER.debug(
                "Skipping overlapping tariff update wake for config entry %s",
                entry.entry_id,
            )
            return

        async with lock:
            try:
                await async_run_scheduled_tariff_update(hass, entry, now=now)
            except LookupError:
                # A config entry may legitimately have no confirmed contract/all-in
                # tariff yet. The scheduler remains dormant until confirmation.
                _LOGGER.debug(
                    "No confirmed active tariff to check for config entry %s",
                    entry.entry_id,
                )
            except Exception:
                # Operational HTTP failures are converted into durable error records
                # by the orchestrator. Reaching here means preparation/config state
                # was invalid; fail safely and never create a misleading notification.
                _LOGGER.exception(
                    "Scheduled tariff source check failed for config entry %s",
                    entry.entry_id,
                )

    unsubscribe = async_track_time_interval(
        hass,
        _scheduled,
        TARIFF_UPDATE_WAKE_INTERVAL,
        name="FRAKON Energy tariff source update wake",
    )
    entry.async_on_unload(unsubscribe)
