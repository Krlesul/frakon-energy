from datetime import datetime, timedelta, timezone

import pytest

from custom_components.frakon_energy.load_execution_approval import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_EXPIRED,
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_REVOKED,
    APPROVAL_STATUS_USED,
    VALIDATION_ALREADY_USED,
    VALIDATION_EXPIRED,
    VALIDATION_NOT_APPROVED,
    VALIDATION_PLAN_STARTED,
    VALIDATION_REVOKED,
    VALIDATION_SCOPE_CHANGED,
    LoadExecutionApprovalRegistry,
    approval_scope_from_evaluation,
    approve_approval,
    consume_approval,
    create_approval,
    revoke_approval,
    validate_approval,
)

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 8, 7, 18, 30, tzinfo=TZ)
START = datetime(2026, 8, 7, 20, 0, tzinfo=TZ)
END = datetime(2026, 8, 7, 22, 0, tzinfo=TZ)


def _evaluation(*, power_kw: float = 11.0, cost: float = 44.0, entity_id: str = "switch.ev_charging") -> dict[str, object]:
    return {
        "status": "approval_required",
        "profile_id": "ev-home",
        "entity_id": entity_id,
        "reasons": [],
        "plan": {
            "starts_at": START.isoformat(),
            "ends_at": END.isoformat(),
            "power_kw": power_kw,
            "duration_minutes": 120,
            "average_czk_kwh": 2.0,
            "estimated_cost_czk": cost,
        },
        "policy": {
            "profile_id": "ev-home",
            "mode": "approval_required",
            "max_power_kw": 11.0,
            "max_duration_minutes": 120,
            "require_entity_binding": True,
            "require_entity_available": True,
        },
        "execution_performed": False,
        "executor_available": False,
    }


def test_scope_fingerprint_is_deterministic_and_sensitive() -> None:
    first = approval_scope_from_evaluation("entry-1", _evaluation())
    same = approval_scope_from_evaluation("entry-1", _evaluation())
    changed = approval_scope_from_evaluation("entry-1", _evaluation(power_kw=7.2))

    assert first.fingerprint() == same.fingerprint()
    assert first.fingerprint() != changed.fingerprint()


def test_scope_requires_clean_approval_required_evaluation() -> None:
    blocked = _evaluation()
    blocked["status"] = "blocked"
    with pytest.raises(ValueError, match="must require approval"):
        approval_scope_from_evaluation("entry-1", blocked)

    missing_entity = _evaluation(entity_id="")
    with pytest.raises(ValueError, match="entity_id is required"):
        approval_scope_from_evaluation("entry-1", missing_entity)


def test_create_approval_is_pending_runtime_only_and_ttl_bounded() -> None:
    approval = create_approval(
        "entry-1",
        _evaluation(),
        ttl_seconds=300,
        now=NOW,
        approval_id="approval-1",
    )

    assert approval.status == APPROVAL_STATUS_PENDING
    assert approval.approval_id == "approval-1"
    assert approval.scope_hash == approval.scope.fingerprint()
    assert datetime.fromisoformat(approval.expires_at) == NOW + timedelta(minutes=5)
    payload = approval.as_dict(now=NOW)
    assert payload["runtime_only"] is True
    assert payload["survives_restart"] is False
    assert payload["execution_performed"] is False

    with pytest.raises(ValueError, match="ttl_seconds"):
        create_approval("entry-1", _evaluation(), ttl_seconds=10, now=NOW)
    with pytest.raises(ValueError, match="ttl_seconds"):
        create_approval("entry-1", _evaluation(), ttl_seconds=901, now=NOW)


def test_expiry_is_capped_at_plan_start() -> None:
    close_start = NOW + timedelta(minutes=2)
    evaluation = _evaluation()
    plan = dict(evaluation["plan"])  # type: ignore[arg-type]
    plan["starts_at"] = close_start.isoformat()
    plan["ends_at"] = (close_start + timedelta(hours=2)).isoformat()
    evaluation["plan"] = plan

    approval = create_approval("entry-1", evaluation, ttl_seconds=900, now=NOW, approval_id="approval-1")
    assert datetime.fromisoformat(approval.expires_at) == close_start


def test_approve_and_validate_same_scope() -> None:
    approval = create_approval("entry-1", _evaluation(), now=NOW, approval_id="approval-1")
    scope_hash = approval.scope_hash
    approved = approve_approval(
        approval,
        current_scope_hash=scope_hash,
        approved_by="user-123",
        now=NOW + timedelta(seconds=5),
    )

    assert approved.status == APPROVAL_STATUS_APPROVED
    assert approved.approved_by == "user-123"
    validation = validate_approval(
        approved,
        current_scope_hash=scope_hash,
        now=NOW + timedelta(seconds=10),
    )
    assert validation.valid is True
    assert validation.reasons == ()


def test_pending_scope_change_expiry_and_plan_start_fail_closed() -> None:
    approval = create_approval("entry-1", _evaluation(), now=NOW, approval_id="approval-1")

    pending = validate_approval(approval, current_scope_hash=approval.scope_hash, now=NOW)
    assert pending.valid is False
    assert VALIDATION_NOT_APPROVED in pending.reasons

    with pytest.raises(ValueError, match="scope changed"):
        approve_approval(
            approval,
            current_scope_hash="different",
            approved_by="user-123",
            now=NOW + timedelta(seconds=5),
        )

    approved = approve_approval(
        approval,
        current_scope_hash=approval.scope_hash,
        approved_by="user-123",
        now=NOW + timedelta(seconds=5),
    )
    changed = validate_approval(approved, current_scope_hash="different", now=NOW + timedelta(seconds=10))
    assert changed.valid is False
    assert VALIDATION_SCOPE_CHANGED in changed.reasons

    expired = validate_approval(
        approved,
        current_scope_hash=approval.scope_hash,
        now=datetime.fromisoformat(approval.expires_at) + timedelta(seconds=1),
    )
    assert expired.status == APPROVAL_STATUS_EXPIRED
    assert VALIDATION_EXPIRED in expired.reasons

    after_start = validate_approval(approved, current_scope_hash=approval.scope_hash, now=START)
    assert after_start.valid is False
    assert VALIDATION_PLAN_STARTED in after_start.reasons


def test_revoke_and_one_time_consume() -> None:
    approval = create_approval("entry-1", _evaluation(), now=NOW, approval_id="approval-1")
    approved = approve_approval(
        approval,
        current_scope_hash=approval.scope_hash,
        approved_by="user-123",
        now=NOW + timedelta(seconds=5),
    )
    revoked = revoke_approval(approved, now=NOW + timedelta(seconds=10))
    assert revoked.status == APPROVAL_STATUS_REVOKED
    revoked_validation = validate_approval(revoked, current_scope_hash=approval.scope_hash, now=NOW + timedelta(seconds=11))
    assert VALIDATION_REVOKED in revoked_validation.reasons

    approval2 = create_approval("entry-1", _evaluation(), now=NOW, approval_id="approval-2")
    approved2 = approve_approval(
        approval2,
        current_scope_hash=approval2.scope_hash,
        approved_by="user-123",
        now=NOW + timedelta(seconds=5),
    )
    used = consume_approval(approved2, current_scope_hash=approval2.scope_hash, now=NOW + timedelta(seconds=10))
    assert used.status == APPROVAL_STATUS_USED
    second = validate_approval(used, current_scope_hash=approval2.scope_hash, now=NOW + timedelta(seconds=11))
    assert VALIDATION_ALREADY_USED in second.reasons


def test_registry_is_runtime_only_and_filters_entries() -> None:
    registry = LoadExecutionApprovalRegistry()
    first = registry.create("entry-1", _evaluation(), now=NOW)
    second = registry.create("entry-2", _evaluation(), now=NOW)

    assert first in registry.list(entry_id="entry-1")
    assert second not in registry.list(entry_id="entry-1")
    assert len(registry.list()) == 2

    fresh_after_restart = LoadExecutionApprovalRegistry()
    assert fresh_after_restart.list() == ()
