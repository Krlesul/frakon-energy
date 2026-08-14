"""Persistent Home Assistant notifications for confirmed tariff source changes."""

from __future__ import annotations

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .tariff_source_watch import STATUS_CHANGE_DETECTED
from .tariff_source_watch_store import tariff_source_watch_record_from_options
from .tariff_update_orchestrator import TariffUpdateCheckRun

_NOTIFICATION_PREFIX = f"{DOMAIN}_tariff_update"


def tariff_update_notification_id(entry: ConfigEntry) -> str:
    """Return the single replaceable tariff-update notification id for an entry."""
    return f"{_NOTIFICATION_PREFIX}_{entry.entry_id}"


def async_sync_tariff_update_notification(
    hass: HomeAssistant,
    entry: ConfigEntry,
    run: TariffUpdateCheckRun,
) -> bool:
    """Create one notification only when a genuinely new pending hash appears.

    The durable watch store remains the authority for whether a proposal is
    pending. Repeated weekly observations of the same pending document do not
    create another notification. When the active watch no longer has a pending
    proposal, any older notification for the config entry is dismissed.

    Returns True only when this call created or replaced the notification.
    """
    if not isinstance(run, TariffUpdateCheckRun):
        raise ValueError("run must be TariffUpdateCheckRun")
    if run.activation_performed is not False:
        raise ValueError("tariff update notification cannot accompany activation")

    notification_id = tariff_update_notification_id(entry)
    current = tariff_source_watch_record_from_options(
        run.updated_options,
        run.check.watch_fingerprint,
    )
    pending_sha256 = current.pending_sha256

    if pending_sha256 is None:
        persistent_notification.async_dismiss(
            hass,
            notification_id=notification_id,
        )
        return False

    if (
        run.check.status != STATUS_CHANGE_DETECTED
        or run.check.requires_confirmation is not True
    ):
        return False

    observed_sha256 = run.check.observed_sha256
    if observed_sha256 is None or observed_sha256 != pending_sha256:
        raise ValueError("change-detected notification must match durable pending hash")

    if run.prepared.record.pending_sha256 == observed_sha256:
        return False

    watch = run.prepared.record.watch
    persistent_notification.async_create(
        hass,
        (
            f"The official tariff document for **{watch.product_name}** has changed. "
            "FRAKON Energy has kept the currently confirmed tariff active and stored "
            "the changed document only as a pending proposal for review.\n\n"
            f"Source: [{watch.document_name}]({watch.source_url})\n\n"
            "No electricity price has been changed automatically."
        ),
        title="FRAKON Energy: tariff document changed",
        notification_id=notification_id,
    )
    return True
