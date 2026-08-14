"""User-notification decisions for newly detected tariff source versions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .tariff_source_watch import STATUS_CHANGE_DETECTED, tariff_source_watch_fingerprint
from .tariff_source_watch_store import tariff_source_watch_records_from_options


@dataclass(frozen=True, slots=True)
class TariffUpdateNotification:
    """Read-only notification payload; it carries no pricing authority."""

    title: str
    message: str
    observed_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("notification title must not be empty")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("notification message must not be empty")
        if (
            not isinstance(self.observed_sha256, str)
            or len(self.observed_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.observed_sha256)
        ):
            raise ValueError("observed_sha256 must be a lowercase SHA-256 digest")


def pending_tariff_hashes(options: Mapping[str, Any]) -> dict[str, str | None]:
    """Snapshot pending hashes by stable watch identity before a source check."""
    if not isinstance(options, Mapping):
        raise ValueError("options must be a mapping")
    return {
        tariff_source_watch_fingerprint(record.watch): record.pending_sha256
        for record in tariff_source_watch_records_from_options(options)
    }


def notification_for_new_pending_tariff(
    run: Any,
    *,
    pending_before: Mapping[str, str | None],
) -> TariffUpdateNotification | None:
    """Return a notification only when this check creates a new pending hash.

    Match score, parser authority and network timing do not grant notification or
    activation authority. The decision is based solely on a fail-closed source
    check result and the durable pending snapshot captured before that check.
    """
    check = getattr(run, "check", None)
    prepared = getattr(run, "prepared", None)
    activation_performed = getattr(run, "activation_performed", None)
    if check is None or prepared is None:
        raise ValueError("run must contain check and prepared state")
    if activation_performed is not False:
        raise ValueError("tariff update notification requires non-activating run")
    if not isinstance(pending_before, Mapping):
        raise ValueError("pending_before must be a mapping")

    if (
        getattr(check, "status", None) != STATUS_CHANGE_DETECTED
        or getattr(check, "requires_confirmation", None) is not True
    ):
        return None

    observed = getattr(check, "observed_sha256", None)
    watch_fingerprint = getattr(check, "watch_fingerprint", None)
    if (
        not isinstance(observed, str)
        or len(observed) != 64
        or any(char not in "0123456789abcdef" for char in observed)
    ):
        raise ValueError("change_detected check requires observed SHA-256")
    if not isinstance(watch_fingerprint, str):
        raise ValueError("change_detected check requires watch fingerprint")
    if pending_before.get(watch_fingerprint) == observed:
        return None

    record = getattr(prepared, "record", None)
    watch = getattr(record, "watch", None)
    if watch is None:
        raise ValueError("prepared run must contain source watch")

    product_name = getattr(watch, "product_name", None)
    source_name = getattr(watch, "source_name", None)
    document_name = getattr(watch, "document_name", None)
    source_url = getattr(watch, "source_url", None)
    for field_name, value in (
        ("product_name", product_name),
        ("source_name", source_name),
        ("document_name", document_name),
        ("source_url", source_url),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"source watch {field_name} must not be empty")

    return TariffUpdateNotification(
        title="FRAKON Energy: tariff update available",
        message=(
            f"A newer official tariff document was detected for **{product_name}** "
            f"from **{source_name}**. The active tariff has not changed and will "
            "remain unchanged until the new version is reviewed and confirmed.\n\n"
            f"Source: [{document_name}]({source_url})\n\n"
            f"Detected document SHA-256: `{observed}`"
        ),
        observed_sha256=observed,
    )
