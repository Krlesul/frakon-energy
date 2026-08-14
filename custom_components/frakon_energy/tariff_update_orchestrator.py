"""Orchestrate safe update checks for the currently confirmed electricity tariff."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Mapping

from .all_in_catalog import (
    PersistedAllInTariff,
    all_in_tariff_fingerprint,
    confirmed_all_in_tariff_for_context_from_options,
)
from .contracts import (
    ElectricityContract,
    confirmed_contract_from_options,
    contract_fingerprint,
)
from .tariff_http_transport import DEFAULT_TARIFF_HTTP_TIMEOUT_SECONDS
from .tariff_source_watch import (
    TariffSourceCheckResult,
    TariffSourceWatch,
    source_watch_from_confirmed_all_in,
    tariff_source_check_error,
    tariff_source_watch_fingerprint,
)
from .tariff_source_watch_fetch import (
    TariffSourceWatchFetchOutcome,
    async_fetch_tariff_source_watch,
    build_tariff_source_watch_fetch_request,
)
from .tariff_source_watch_store import (
    OPTION_TARIFF_SOURCE_WATCHES,
    TariffSourceWatchRecord,
    append_tariff_source_watch,
    record_tariff_source_check,
    tariff_source_watch_record_from_options,
    tariff_source_watch_records_from_options,
)

_MAX_OPERATIONAL_ERROR_CHARS = 500


def _validate_alignment(
    contract: ElectricityContract,
    tariff: PersistedAllInTariff,
) -> None:
    assembly = tariff.assembly
    if contract.product_name.strip() != assembly.product_name.strip():
        raise ValueError("confirmed contract and all-in tariff product do not match")
    if contract.distribution_tariff != assembly.distribution_tariff:
        raise ValueError("confirmed contract and all-in distribution tariff do not match")
    if contract.breaker.code != assembly.breaker_code:
        raise ValueError("confirmed contract and all-in breaker do not match")


def _replace_watch_record(
    options: Mapping[str, Any],
    *,
    fingerprint: str,
    record: TariffSourceWatchRecord,
) -> dict[str, Any]:
    records = list(tariff_source_watch_records_from_options(options))
    matched = False
    for index, current in enumerate(records):
        if tariff_source_watch_fingerprint(current.watch) != fingerprint:
            continue
        records[index] = record
        matched = True
        break
    if not matched:
        raise LookupError(f"tariff source watch not found: {fingerprint}")
    updated = dict(options)
    updated[OPTION_TARIFF_SOURCE_WATCHES] = [item.as_dict() for item in records]
    return updated


def _operational_error_text(err: Exception) -> str:
    text = f"{type(err).__name__}: {err}".strip()
    if len(text) <= _MAX_OPERATIONAL_ERROR_CHARS:
        return text
    return text[: _MAX_OPERATIONAL_ERROR_CHARS - 1] + "…"


@dataclass(frozen=True, slots=True)
class PreparedActiveTariffSourceWatch:
    """One active watch reconciled exclusively from confirmed contract/catalog state."""

    day: date
    contract_fingerprint: str
    all_in_fingerprint: str
    watch_fingerprint: str
    record: TariffSourceWatchRecord
    updated_options: dict[str, Any]
    rebind_performed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.day, date):
            raise ValueError("day must be a date")
        for field_name in (
            "contract_fingerprint",
            "all_in_fingerprint",
            "watch_fingerprint",
        ):
            value = getattr(self, field_name)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
            ):
                raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
        if not isinstance(self.record, TariffSourceWatchRecord):
            raise ValueError("record must be TariffSourceWatchRecord")
        if tariff_source_watch_fingerprint(self.record.watch) != self.watch_fingerprint:
            raise ValueError("prepared watch record fingerprint mismatch")
        if not isinstance(self.updated_options, dict):
            raise ValueError("updated_options must be a dict")
        if not isinstance(self.rebind_performed, bool):
            raise ValueError("rebind_performed must be boolean")


@dataclass(frozen=True, slots=True)
class TariffUpdateCheckRun:
    """Result of one active-source check plus the options state to persist."""

    prepared: PreparedActiveTariffSourceWatch
    check: TariffSourceCheckResult
    updated_options: dict[str, Any]
    outcome: TariffSourceWatchFetchOutcome | None = None
    error_captured: bool = False
    activation_performed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.prepared, PreparedActiveTariffSourceWatch):
            raise ValueError("prepared must be PreparedActiveTariffSourceWatch")
        if not isinstance(self.check, TariffSourceCheckResult):
            raise ValueError("check must be TariffSourceCheckResult")
        if self.check.watch_fingerprint != self.prepared.watch_fingerprint:
            raise ValueError("check does not belong to prepared active source watch")
        if not isinstance(self.updated_options, dict):
            raise ValueError("updated_options must be a dict")
        if self.outcome is not None:
            if not isinstance(self.outcome, TariffSourceWatchFetchOutcome):
                raise ValueError("outcome must be TariffSourceWatchFetchOutcome")
            if self.outcome.check != self.check:
                raise ValueError("outcome check must equal run check")
        if self.error_captured and self.outcome is not None:
            raise ValueError("captured-error run cannot also contain fetch outcome")
        if self.activation_performed is not False:
            raise ValueError("tariff update check cannot activate pricing")

    @property
    def parser_authorized(self) -> bool:
        return self.outcome is not None and self.outcome.parser_authorized


def prepare_active_tariff_source_watch(
    options: Mapping[str, Any],
    *,
    day: date,
) -> PreparedActiveTariffSourceWatch:
    """Resolve/reconcile the one watch authorized by current confirmed state."""
    if not isinstance(day, date):
        raise ValueError("day must be a date")
    contract = confirmed_contract_from_options(options, day)
    tariff = confirmed_all_in_tariff_for_context_from_options(
        options,
        supplier=contract.supplier.value,
        product_name=contract.product_name,
        distribution_tariff=contract.distribution_tariff,
        breaker_code=contract.breaker.code,
        day=day,
    )
    _validate_alignment(contract, tariff)

    authoritative_watch = source_watch_from_confirmed_all_in(
        tariff,
        supplier=contract.supplier.value,
    )
    watch_fingerprint = tariff_source_watch_fingerprint(authoritative_watch)
    current_records = tariff_source_watch_records_from_options(options)
    current = next(
        (
            item
            for item in current_records
            if tariff_source_watch_fingerprint(item.watch) == watch_fingerprint
        ),
        None,
    )

    updated_options = dict(options)
    rebind_performed = False
    if current is None:
        updated_options = append_tariff_source_watch(updated_options, authoritative_watch)
        current = tariff_source_watch_record_from_options(
            updated_options,
            watch_fingerprint,
        )
    elif current.watch.active_sha256 != authoritative_watch.active_sha256:
        # Only confirmed catalog state may perform this rebind. If the newly
        # confirmed checksum equals the pending proposal, its HTTP validators are
        # known to refer to that exact body and may be carried forward safely.
        if current.pending_sha256 == authoritative_watch.active_sha256:
            authoritative_watch = replace(
                authoritative_watch,
                etag=current.watch.etag,
                last_modified=current.watch.last_modified,
            )
        replacement = TariffSourceWatchRecord(watch=authoritative_watch)
        updated_options = _replace_watch_record(
            updated_options,
            fingerprint=watch_fingerprint,
            record=replacement,
        )
        current = replacement
        rebind_performed = True

    return PreparedActiveTariffSourceWatch(
        day=day,
        contract_fingerprint=contract_fingerprint(contract),
        all_in_fingerprint=all_in_tariff_fingerprint(tariff),
        watch_fingerprint=watch_fingerprint,
        record=current,
        updated_options=updated_options,
        rebind_performed=rebind_performed,
    )


async def async_check_active_tariff_source(
    options: Mapping[str, Any],
    *,
    day: date,
    session: Any,
    checked_at: datetime,
    timeout_seconds: float = DEFAULT_TARIFF_HTTP_TIMEOUT_SECONDS,
) -> TariffUpdateCheckRun:
    """Run one active source check and return durable options without activation."""
    if not isinstance(checked_at, datetime) or checked_at.tzinfo is None:
        raise ValueError("checked_at must be a timezone-aware datetime")
    prepared = prepare_active_tariff_source_watch(options, day=day)
    watch: TariffSourceWatch = prepared.record.watch
    request = build_tariff_source_watch_fetch_request(watch)

    try:
        outcome = await async_fetch_tariff_source_watch(
            watch=watch,
            request=request,
            session=session,
            checked_at=checked_at,
            timeout_seconds=timeout_seconds,
        )
    except Exception as err:
        check = tariff_source_check_error(
            watch,
            checked_at=checked_at,
            error=_operational_error_text(err),
        )
        updated_options = record_tariff_source_check(
            prepared.updated_options,
            check,
        )
        return TariffUpdateCheckRun(
            prepared=prepared,
            check=check,
            updated_options=updated_options,
            outcome=None,
            error_captured=True,
        )

    updated_options = record_tariff_source_check(
        prepared.updated_options,
        outcome.check,
    )
    return TariffUpdateCheckRun(
        prepared=prepared,
        check=outcome.check,
        updated_options=updated_options,
        outcome=outcome,
        error_captured=False,
    )
