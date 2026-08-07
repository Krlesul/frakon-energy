from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from custom_components.frakon_energy import load_execution_preflight_ws_api as preflight_ws
from custom_components.frakon_energy.load_execution_attempt import ATTEMPT_STATE_PREPARED
from custom_components.frakon_energy.load_execution_preflight import (
    PREFLIGHT_BLOCKED,
    PREFLIGHT_READY,
    REASON_APPROVAL_INVALID,
    REASON_UNSUPPORTED_ENTITY_DOMAIN,
)

NOW = datetime(2026, 8, 7, 18, 30, tzinfo=timezone(timedelta(hours=2)))


class FakeRecord:
    profile_id = "ev-home"
    plan_starts_at = "2026-08-07T20:00:00+02:00"
    plan_ends_at = "2026-08-07T22:00:00+02:00"
    approval = SimpleNamespace(snapshot_digest="a" * 64)

    def as_dict(self, *, now: datetime | None = None) -> dict[str, object]:
        return {
            "entry_id": "entry-1",
            "profile_id": self.profile_id,
            "status": "approved",
            "approval": {
                "approval_id": "approval-1",
                "snapshot_digest": self.approval.snapshot_digest,
            },
            "runtime_only": True,
            "execution_performed": False,
            "can_execute": False,
        }


class FakeHass:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}


def _verified(*, valid: bool = True, reason: str = "ok", entity_id: str = "switch.ev_charging") -> dict[str, object]:
    return {
        "verification": {
            "valid": valid,
            "reason": reason,
            "consumed": False,
            "execution_performed": False,
        },
        "evaluation": {
            "status": "approval_required" if valid else "blocked",
            "profile_id": "ev-home",
            "entity_id": entity_id,
            "reasons": [] if valid else ["policy_not_eligible"],
            "plan": {
                "starts_at": "2026-08-07T20:00:00+02:00",
                "ends_at": "2026-08-07T22:00:00+02:00",
            },
            "execution_performed": False,
            "executor_available": False,
        },
    }


def _patch(monkeypatch: pytest.MonkeyPatch, response: dict[str, object]) -> None:
    monkeypatch.setattr(preflight_ws, "_record", lambda *args, **kwargs: FakeRecord())

    async def fake_verify(*args: object, **kwargs: object) -> dict[str, object]:
        return response

    monkeypatch.setattr(preflight_ws, "async_verify_execution_approval", fake_verify)


@pytest.mark.asyncio
async def test_valid_signed_approval_prepares_dry_run_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = FakeHass()
    _patch(monkeypatch, _verified())

    result = await preflight_ws.async_prepare_execution_preflight(
        hass,
        entry_id="entry-1",
        approval_id="approval-1",
        now=NOW,
    )

    assert result["preflight"]["status"] == PREFLIGHT_READY
    assert result["preflight"]["proposal"]["domain"] == "switch"
    assert result["preflight"]["proposal"]["service"] == "turn_on"
    assert result["preflight"]["attempt"]["state"] == ATTEMPT_STATE_PREPARED
    assert result["dry_run"] is True
    assert result["approval_consumed"] is False
    assert result["execution_performed"] is False
    assert result["can_execute"] is False
    assert len(preflight_ws._ledger(hass, "entry-1").list()) == 1


@pytest.mark.asyncio
async def test_repeated_preflight_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = FakeHass()
    _patch(monkeypatch, _verified())

    first = await preflight_ws.async_prepare_execution_preflight(
        hass, entry_id="entry-1", approval_id="approval-1", now=NOW
    )
    second = await preflight_ws.async_prepare_execution_preflight(
        hass, entry_id="entry-1", approval_id="approval-1", now=NOW + timedelta(seconds=1)
    )

    assert first["preflight"]["attempt"]["attempt_id"] == second["preflight"]["attempt"]["attempt_id"]
    assert first["preflight"]["attempt"]["idempotency_key"] == second["preflight"]["attempt"]["idempotency_key"]
    assert len(preflight_ws._ledger(hass, "entry-1").list()) == 1


@pytest.mark.asyncio
async def test_invalid_approval_is_blocked_without_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = FakeHass()
    _patch(monkeypatch, _verified(valid=False, reason="snapshot_mismatch"))

    result = await preflight_ws.async_prepare_execution_preflight(
        hass, entry_id="entry-1", approval_id="approval-1", now=NOW
    )

    assert result["preflight"]["status"] == PREFLIGHT_BLOCKED
    assert result["preflight"]["reasons"] == [REASON_APPROVAL_INVALID]
    assert result["preflight"]["attempt"] is None
    assert len(preflight_ws._ledger(hass, "entry-1").list()) == 0


@pytest.mark.asyncio
async def test_unsupported_entity_domain_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = FakeHass()
    _patch(monkeypatch, _verified(entity_id="climate.boiler"))

    result = await preflight_ws.async_prepare_execution_preflight(
        hass, entry_id="entry-1", approval_id="approval-1", now=NOW
    )

    assert result["preflight"]["status"] == PREFLIGHT_BLOCKED
    assert result["preflight"]["reasons"] == [REASON_UNSUPPORTED_ENTITY_DOMAIN]
    assert result["preflight"]["proposal"] is None
    assert len(preflight_ws._ledger(hass, "entry-1").list()) == 0


def test_attempt_ledgers_are_entry_scoped_and_restart_fail_closed() -> None:
    first = FakeHass()
    second = FakeHass()
    assert preflight_ws._ledger(first, "entry-1") is not preflight_ws._ledger(first, "entry-2")
    assert preflight_ws._ledger(first, "entry-1") is not preflight_ws._ledger(second, "entry-1")
    assert preflight_ws._attempts_payload(first, "entry-1")["can_execute"] is False
