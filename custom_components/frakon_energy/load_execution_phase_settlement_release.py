"""Proof-gated durable release of settled phase-capacity reservations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import time
from typing import Any

from homeassistant.core import HomeAssistant

from .load_execution_lifecycle import STATE_VERIFIED
from .load_execution_lifecycle_runtime import lifecycle_repository
from .load_execution_phase_capacity_reservation import (
    PhaseCapacityReservationError,
    phase_capacity_reservation_repository,
)
from .load_execution_phase_settlement_confirmation import (
    phase_settlement_confirmation_repository,
)
from .load_execution_phase_settlement_proof import async_phase_settlement_proof

STATUS_CONFIRMATION_NOT_READY = "confirmation_not_ready"
STATUS_LIFECYCLE_NOT_VERIFIED = "lifecycle_not_verified"
STATUS_RECHECK_NOT_READY = "final_settlement_recheck_not_ready"
STATUS_ALREADY_ABSENT = "reservation_already_absent"
STATUS_RELEASED = "released"


class PhaseSettlementReleaseError(RuntimeError):
    """Raised when a confirmed settlement cannot be safely persisted."""


@dataclass(frozen=True, slots=True)
class PhaseSettlementReleaseResult:
    lifecycle_id: str
    status: str
    released: bool
    reason: str
    confirmation: dict[str, Any] | None
    final_proof: dict[str, Any] | None
    released_reservation: dict[str, Any] | None
    service_call_performed: bool = False
    execution_performed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


async def async_release_confirmed_phase_reservation(
    hass: HomeAssistant,
    *,
    entry_id: str,
    lifecycle_id: str,
) -> PhaseSettlementReleaseResult:
    """Release only after durable confirmation and a fresh final telemetry proof."""
    if not entry_id or not lifecycle_id:
        raise ValueError("entry_id and lifecycle_id are required")

    confirmation = await phase_settlement_confirmation_repository(hass, entry_id).async_get(lifecycle_id)
    if confirmation is None or confirmation.confirmed_at is None:
        return PhaseSettlementReleaseResult(
            lifecycle_id=lifecycle_id,
            status=STATUS_CONFIRMATION_NOT_READY,
            released=False,
            reason="Durable two-sample settlement confirmation is not ready.",
            confirmation=confirmation.as_dict() if confirmation is not None else None,
            final_proof=None,
            released_reservation=None,
        )

    records = await lifecycle_repository(hass, entry_id).async_list()
    lifecycle = next((record for record in records if record.lifecycle_id == lifecycle_id), None)
    if lifecycle is None or lifecycle.state != STATE_VERIFIED:
        return PhaseSettlementReleaseResult(
            lifecycle_id=lifecycle_id,
            status=STATUS_LIFECYCLE_NOT_VERIFIED,
            released=False,
            reason="Execution lifecycle is not durably verified at release time.",
            confirmation=confirmation.as_dict(),
            final_proof=None,
            released_reservation=None,
        )
    if lifecycle.attempt_id != confirmation.attempt_id:
        raise PhaseSettlementReleaseError("settlement confirmation attempt binding mismatch")

    repository = phase_capacity_reservation_repository(hass, entry_id)
    active = await repository.async_snapshot(now=max(1, int(time.time())))
    reservation = next((item for item in active if item.lifecycle_id == lifecycle_id), None)
    if reservation is None:
        return PhaseSettlementReleaseResult(
            lifecycle_id=lifecycle_id,
            status=STATUS_ALREADY_ABSENT,
            released=False,
            reason="The phase reservation is already absent or expired.",
            confirmation=confirmation.as_dict(),
            final_proof=None,
            released_reservation=None,
        )
    if reservation.attempt_id != confirmation.attempt_id:
        raise PhaseSettlementReleaseError("phase reservation attempt binding mismatch")

    proof = await async_phase_settlement_proof(
        hass,
        entry_id=entry_id,
        lifecycle_id=lifecycle_id,
    )
    if not proof.candidate:
        return PhaseSettlementReleaseResult(
            lifecycle_id=lifecycle_id,
            status=STATUS_RECHECK_NOT_READY,
            released=False,
            reason=proof.reason,
            confirmation=confirmation.as_dict(),
            final_proof=proof.as_dict(),
            released_reservation=None,
        )

    try:
        released, changed = await repository.async_release(
            lifecycle_id=lifecycle_id,
            attempt_id=confirmation.attempt_id,
        )
    except PhaseCapacityReservationError as err:
        raise PhaseSettlementReleaseError(f"phase reservation release rejected: {err}") from err
    except Exception as err:
        raise PhaseSettlementReleaseError(f"phase reservation release persistence unavailable: {err}") from err

    if released is None or not changed:
        return PhaseSettlementReleaseResult(
            lifecycle_id=lifecycle_id,
            status=STATUS_ALREADY_ABSENT,
            released=False,
            reason="The phase reservation became absent before durable release.",
            confirmation=confirmation.as_dict(),
            final_proof=proof.as_dict(),
            released_reservation=None,
        )

    return PhaseSettlementReleaseResult(
        lifecycle_id=lifecycle_id,
        status=STATUS_RELEASED,
        released=True,
        reason="Durably verified two-sample settlement and final telemetry recheck allowed reservation release.",
        confirmation=confirmation.as_dict(),
        final_proof=proof.as_dict(),
        released_reservation=released.as_dict(),
    )
