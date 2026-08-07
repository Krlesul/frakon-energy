from datetime import datetime, timedelta, timezone

from custom_components.frakon_energy.load_execution_attempt import (
    ATTEMPT_STATE_CANCELLED,
    ATTEMPT_STATE_PREPARED,
    ExecutionAttemptLedger,
)
from custom_components.frakon_energy.load_execution_preflight import (
    PREFLIGHT_BLOCKED,
    PREFLIGHT_READY,
    REASON_APPROVAL_INVALID,
    REASON_ENTITY_REQUIRED,
    REASON_UNSUPPORTED_ENTITY_DOMAIN,
    prepare_execution_preflight,
    propose_start_service,
)

NOW = datetime(2026, 8, 7, 18, 30, tzinfo=timezone(timedelta(hours=2)))


def _preflight(ledger: ExecutionAttemptLedger, **overrides: object):
    values = {
        "approval_id": "approval-1",
        "snapshot_digest": "a" * 64,
        "profile_id": "ev-home",
        "entity_id": "switch.ev_charging",
        "planned_starts_at": "2026-08-07T20:00:00+02:00",
        "planned_ends_at": "2026-08-07T22:00:00+02:00",
        "approval_valid": True,
        "approval_verification_reason": "ok",
        "now": NOW,
    }
    values.update(overrides)
    return prepare_execution_preflight(ledger, **values)


def test_service_mapping_is_intentionally_small() -> None:
    switch = propose_start_service("switch.ev_charging")
    assert switch.domain == "switch"
    assert switch.service == "turn_on"
    assert switch.service_data == {"entity_id": "switch.ev_charging"}

    helper = propose_start_service("input_boolean.boiler_enable")
    assert helper.domain == "input_boolean"
    assert helper.service == "turn_on"


def test_invalid_or_unsupported_entity_fails_closed() -> None:
    ledger = ExecutionAttemptLedger()
    missing = _preflight(ledger, entity_id=None)
    assert missing.status == PREFLIGHT_BLOCKED
    assert missing.reasons == (REASON_ENTITY_REQUIRED,)
    assert missing.attempt is None

    unsupported = _preflight(ledger, entity_id="climate.boiler")
    assert unsupported.status == PREFLIGHT_BLOCKED
    assert unsupported.reasons == (REASON_UNSUPPORTED_ENTITY_DOMAIN,)
    assert unsupported.proposal is None
    assert ledger.list() == ()


def test_invalid_approval_never_creates_attempt() -> None:
    ledger = ExecutionAttemptLedger()
    result = _preflight(
        ledger,
        approval_valid=False,
        approval_verification_reason="snapshot_mismatch",
    )
    assert result.status == PREFLIGHT_BLOCKED
    assert result.reasons == (REASON_APPROVAL_INVALID,)
    assert result.attempt is None
    assert ledger.list() == ()
    assert result.execution_performed is False
    assert result.approval_consumed is False


def test_valid_preflight_registers_prepared_attempt_without_execution() -> None:
    ledger = ExecutionAttemptLedger()
    result = _preflight(ledger)

    assert result.status == PREFLIGHT_READY
    assert result.reasons == ()
    assert result.attempt is not None
    assert result.attempt.state == ATTEMPT_STATE_PREPARED
    assert result.proposal is not None
    assert result.proposal.domain == "switch"
    assert result.proposal.service == "turn_on"
    assert result.dry_run is True
    assert result.approval_consumed is False
    assert result.execution_performed is False
    assert result.executor_available is False
    assert result.can_execute is False
    assert len(ledger.list()) == 1


def test_same_approval_and_snapshot_is_idempotent() -> None:
    ledger = ExecutionAttemptLedger()
    first = _preflight(ledger)
    second = _preflight(ledger)

    assert first.attempt is not None
    assert second.attempt is not None
    assert second.attempt.attempt_id == first.attempt.attempt_id
    assert second.attempt.idempotency_key == first.attempt.idempotency_key
    assert len(ledger.list()) == 1


def test_prepared_attempt_can_be_cancelled_without_execution() -> None:
    ledger = ExecutionAttemptLedger()
    result = _preflight(ledger)
    assert result.attempt is not None

    cancelled = ledger.cancel(result.attempt.attempt_id, now=NOW + timedelta(seconds=5))
    assert cancelled.state == ATTEMPT_STATE_CANCELLED
    assert cancelled.execution_performed is False
