"""Exact selected-document orchestration for tariff review.

This layer deliberately re-runs supplier discovery from the immutable contract
before any HTTP request. Frontends therefore cannot turn an arbitrary URL or
stale candidate object into download authority: the selected fingerprint must
still identify one of the currently verified supplier candidates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Awaitable, Callable

from .contracts import ElectricityContract
from .tariff_candidate_selection import select_tariff_candidate
from .tariff_discovery import async_discover_contract_tariff_candidates
from .tariff_download import ValidatedTariffDownload
from .tariff_fetch import (
    TariffFetchRequest,
    TariffNotModified,
    build_tariff_fetch_request,
)
from .tariff_sources import TariffAdapterRegistry, TariffDocumentCandidate

SelectedTariffFetch = Callable[
    ...,
    Awaitable[ValidatedTariffDownload | TariffNotModified],
]


@dataclass(frozen=True, slots=True)
class SelectedTariffDownloadRun:
    """One explicitly selected supplier-document fetch with no price authority."""

    selected_fingerprint: str
    candidate: TariffDocumentCandidate
    request: TariffFetchRequest
    outcome: ValidatedTariffDownload | TariffNotModified
    discovery_performed: bool = True
    explicit_selection_verified: bool = True
    persistence_performed: bool = False
    activation_performed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, TariffDocumentCandidate):
            raise ValueError("candidate must be TariffDocumentCandidate")
        if not isinstance(self.request, TariffFetchRequest):
            raise ValueError("request must be TariffFetchRequest")
        if not isinstance(self.outcome, (ValidatedTariffDownload, TariffNotModified)):
            raise ValueError("outcome must be a validated tariff download result")
        if self.request.selected_fingerprint != self.selected_fingerprint:
            raise ValueError("request fingerprint does not match selected fingerprint")
        if self.request.source_url != self.candidate.document.source_url:
            raise ValueError("request source does not match selected candidate")
        if self.outcome.selected_fingerprint != self.selected_fingerprint:
            raise ValueError("fetch outcome fingerprint does not match selection")
        if self.outcome.source_url != self.candidate.document.source_url:
            raise ValueError("fetch outcome source does not match selected candidate")

    @property
    def body_available(self) -> bool:
        return isinstance(self.outcome, ValidatedTariffDownload)

    @property
    def parser_authorized(self) -> bool:
        return (
            isinstance(self.outcome, ValidatedTariffDownload)
            and self.outcome.parser_authorized is True
        )


def _validate_checked_at(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("checked_at must be a timezone-aware datetime")
    return value


async def async_fetch_selected_contract_tariff(
    contract: ElectricityContract,
    *,
    day: date,
    selected_fingerprint: str,
    registry: TariffAdapterRegistry,
    checked_at: datetime,
    fetch_selected: SelectedTariffFetch,
) -> SelectedTariffDownloadRun:
    """Re-discover and fetch exactly one currently verified supplier candidate.

    The caller may provide only the immutable contract, requested day and stable
    candidate fingerprint. Candidate URL/metadata are re-derived server-side from
    the registered supplier adapter before the bounded HTTP fetch is authorized.
    """
    if not isinstance(contract, ElectricityContract):
        raise ValueError("contract must be ElectricityContract")
    if not isinstance(day, date):
        raise ValueError("day must be a date")
    if not isinstance(registry, TariffAdapterRegistry):
        raise ValueError("registry must be TariffAdapterRegistry")
    if not callable(fetch_selected):
        raise ValueError("fetch_selected must be callable")
    checked = _validate_checked_at(checked_at)

    candidates = await async_discover_contract_tariff_candidates(
        contract,
        day=day,
        registry=registry,
    )
    candidate = select_tariff_candidate(
        candidates,
        fingerprint=selected_fingerprint,
    )
    request = build_tariff_fetch_request(
        candidate,
        selected_fingerprint=selected_fingerprint,
    )
    outcome = await fetch_selected(
        candidate=candidate,
        request=request,
        checked_at=checked,
    )
    if not isinstance(outcome, (ValidatedTariffDownload, TariffNotModified)):
        raise ValueError("selected tariff fetcher returned an invalid outcome")

    return SelectedTariffDownloadRun(
        selected_fingerprint=selected_fingerprint,
        candidate=candidate,
        request=request,
        outcome=outcome,
    )
