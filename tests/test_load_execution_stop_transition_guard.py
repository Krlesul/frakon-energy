from datetime import datetime, timedelta, timezone

import pytest

from custom_components.frakon_energy.load_execution_stop_lifecycle import (
    ExecutionStopLifecycleRecord,
    StopLifecycleError,
)
from custom_components.frakon_energy.load_execution_stop_transition_guard import (
    assert_stop_deadline_reached,
    begin_due_stop_dispatch,
    satisfy_due_stop_without_dispatch,
)

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 8, 8, 1, 0, tzinfo=TZ)
END = START + timedelta(hours=2)


def _owned() -> ExecutionStopLifecycleRecord:
    return ExecutionStopLifecycleRecord(
        stop_lifecycle_id="f76d6da2c879fda8c5fae44f6cdc897a",
        lease_id="a" * 32,
        entry_id="entry-1",
        start_lifecycle_id="b" * 32,
        attempt_id="attempt-1",
        action_snapshot_id="c" * 32,
        profile_id="ev-home",
        entity_id="switch.enyaq_charging",
        approval_snapshot_digest="d" * 64,
        plan_digest="f" * 64,
        starts_at=START.isoformat(),
        ends_at=END.isoformat(),
        service_domain="switch",
        service_name="turn_off",
        desired_state="off",
        state="owned",
        service_call_status="not_started",
        verification_status="pending",
        created_at=int(START.timestamp()),
        updated_at=int(START.timestamp()),
    ).validated()


def test_deadline_guard_rejects_one_second_early() -> None:
    with pytest.raises(StopLifecycleError, match="before ends_at"):
        assert_stop_deadline_reached(
            _owned(),
            now=int(END.timestamp()) - 1,
        )


def test_deadline_guard_accepts_exact_deadline() -> None:
    assert_stop_deadline_reached(
        _owned(),
        now=int(END.timestamp()),
    )


def test_dispatch_transition_rejects_early_and_accepts_exact_deadline() -> None:
    record = _owned()
    with pytest.raises(StopLifecycleError, match="before ends_at"):
        begin_due_stop_dispatch(
            record,
            now=int(END.timestamp()) - 1,
        )

    dispatching = begin_due_stop_dispatch(
        record,
        now=int(END.timestamp()),
    )
    assert dispatching.state == "dispatching"
    assert dispatching.dispatch_started_at == int(END.timestamp())
    assert dispatching.dispatch_attempts == 1
    assert dispatching.service_call_status == "unknown"


def test_already_off_noop_rejects_early_and_accepts_exact_deadline() -> None:
    record = _owned()
    with pytest.raises(StopLifecycleError, match="before ends_at"):
        satisfy_due_stop_without_dispatch(
            record,
            current_state="off",
            now=int(END.timestamp()) - 1,
        )

    satisfied = satisfy_due_stop_without_dispatch(
        record,
        current_state="off",
        now=int(END.timestamp()),
    )
    assert satisfied.state == "satisfied"
    assert satisfied.satisfied_at == int(END.timestamp())
    assert satisfied.dispatch_attempts == 0
    assert satisfied.as_dict()["service_call_performed"] is False


def test_guard_never_weakens_entity_state_validation() -> None:
    with pytest.raises(StopLifecycleError, match="not already"):
        satisfy_due_stop_without_dispatch(
            _owned(),
            current_state="on",
            now=int(END.timestamp()),
        )
