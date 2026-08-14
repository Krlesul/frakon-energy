"""Weekly Home Assistant scheduler for safe tariff source update checks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN
from .tariff_source_watch import STATUS_CHANGE_DETECTED
from .tariff_source_watch_store import tariff_source_watch_records_from_options
from .tariff_source_watch import tariff_source_watch_fingerprint
from .tariff_update_ha import async_check_active_tariff_source_ha
from .tariff_update_orchestrator import TariffUpdateCheckRun

_LOGGER = logging.getLogger(__name__)

TARIFF_UPDATE_INTERVAL = timedelta(days=7)
_NOTIFICATION_PREFIX = f"{DOMAIN}_tariff_update"


@dataclass(frozen=True, slots=True)
class ScheduledTariffUpdateResult:
    """Result of one scheduled update check and its user-notification decision."""

    run: TariffUpdateCheckRun
    notification_created: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.run, TariffUpdateCheckRun):
            raise ValueError("run must be TariffUpdateCheckRun")
        if not isinstance(self.notification_created, bool):
            raise ValueError("notification_created must be boolean")
        if self.run.activation_performed is not False:
            raise ValueError("scheduled tariff update must never activate pricing")


def _pending_by_watch(options: object) -> dict[str, str | None]:
    if not isinstance(options, dict):
        # Home Assistant exposes config-entry options as a mapping-like object;
        # converting here also gives the pure store an immutable snapshot.
        try:
            options = dict(options)  # type: ignore[arg-type]
        except Exception as err:
            raise ValueError("config entry options must be mapping-like") from err

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
    """Run one weekly tariff check and notify only for a newly pending document."""
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime")

    pending_before = _pending_by_watch(entry.options)
    checked_at = now.astimezone(timezone.utc)
    run = await async_check_active_tariff_source_ha(
        hass,
        entry,
        day=now.date(),
        checked_at=checked_at,
    )

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
    """Register one non-overlapping weekly tariff check for a config entry."""
    lock = asyncio.Lock()

    async def _scheduled(now: datetime) -> None:
        if lock.locked():
            _LOGGER.debug(
                "Skipping overlapping tariff source check for config entry %s",
                entry.entry_id,
            )
            return

        async with lock:
            try:
                await async_run_scheduled_tariff_update(hass, entry, now=now)
            except LookupError:
                # A config entry may legitimately have no confirmed contract/all-in
                # tariff yet. Weekly automation stays dormant until confirmation.
                _LOGGER.debug(
                    "No confirmed active tariff to check for config entry %s",
                    entry.entry_id,
                )
            except Exception:
                # Operational HTTP failures are already converted into durable error
                # results by the orchestrator. Reaching this handler means preparation
                # or persisted config state was invalid; fail safely without notifying.
                _LOGGER.exception(
                    "Scheduled tariff source check failed for config entry %s",
                    entry.entry_id,
                )

    unsubscribe = async_track_time_interval(
        hass,
        _scheduled,
        TARIFF_UPDATE_INTERVAL,
        name="FRAKON Energy tariff source update",
    )
    entry.async_on_unload(unsubscribe)
